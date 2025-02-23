"""This metric outputs the BERT classifier prediction of the paraphrased text."""

import os

import numpy as np
import torch
import torch.nn.functional as F
import tqdm
import transformers
from torch.utils.tensorboard import SummaryWriter
from transformers import BertForSequenceClassification, BertTokenizerFast

from fibber import get_root_dir, log, resources
from fibber.datasets import DatasetForBert
from fibber.metrics.bert_classifier_utils_lmag import lmag_fix_sentences
from fibber.metrics.bert_classifier_utils_sem import (
    load_or_build_sem_wordmap, sem_fix_sentences, sem_transform_dataset)
from fibber.metrics.classifier_base import ClassifierBase

logger = log.setup_custom_logger(__name__)


def get_optimizer(optimizer_name, lr, decay, train_step, params, warmup=1000):
    """Create an optimizer and schedule of learning rate for parameters.

    Args:
        optimizer_name (str): choose from ``["adam", "sgd", "adamw"]``.
        lr (float): learning rate.
        decay (float): weight decay.
        train_step (int): number of training steps.
        params (list): a list of parameters in the model.
        warmup (int): number of warm up steps.

    Returns:
        A torch optimizer and a scheduler.
    """
    if optimizer_name == "adam":
        opt = torch.optim.Adam(params=params, lr=lr, weight_decay=decay)
    elif optimizer_name == "sgd":
        opt = torch.optim.SGD(params=params, lr=lr, weight_decay=decay)
    elif optimizer_name == "adamw":
        opt = torch.optim.AdamW(params=params, lr=lr, weight_decay=decay)
    else:
        assert 0, "unkown optimizer"

    sche = transformers.get_linear_schedule_with_warmup(
        opt, num_warmup_steps=warmup, num_training_steps=train_step)

    return opt, sche


def run_evaluate(model, dataloader_iter, eval_steps, summary, global_step, device):
    """Evaluate a model and add error rate and validation loss to Tensorboard.

    Args:
        model (transformers.BertForSequenceClassification): a BERT classification model.
        dataloader_iter (torch.IterableDataset): an iterator of a torch.IterableDataset.
        eval_steps (int): number of training steps.
        summary (torch.utils.tensorboard.SummaryWriter): a Tensorboard SummaryWriter object.
        global_step (int): current training steps.
        device (torch.Device): the device where the model in running on.
    """
    model.eval()

    correct, count = 0, 0
    loss_list = []

    for v_step in range(eval_steps):
        seq, mask, tok_type, label = next(dataloader_iter)
        seq = seq.to(device)
        mask = mask.to(device)
        tok_type = tok_type.to(device)
        label = label.to(device)

        with torch.no_grad():
            outputs = model(seq, mask, tok_type, labels=label)
            loss, logits = outputs[:2]
            loss_list.append(loss.detach().cpu().numpy())
            count += seq.size(0)
            correct += (logits.argmax(dim=1).eq(label)
                        .float().sum().detach().cpu().numpy())

    summary.add_scalar("clf_val/error_rate", 1 - correct / count, global_step)
    summary.add_scalar("clf_val/loss", np.mean(loss_list), global_step)

    model.train()


def load_or_train_bert_clf(model_init,
                           dataset_name,
                           trainset,
                           testset,
                           bert_clf_steps,
                           bert_clf_bs,
                           bert_clf_lr,
                           bert_clf_optimizer,
                           bert_clf_weight_decay,
                           bert_clf_period_summary,
                           bert_clf_period_val,
                           bert_clf_period_save,
                           bert_clf_val_steps,
                           bert_clf_enable_sem,
                           bert_clf_sem_word_map,
                           device):
    """Train BERT classification model on a dataset.

    The trained model will be stored at ``<fibber_root_dir>/bert_clf/<dataset_name>/``. If there's
    a saved model, load and return the model. Otherwise, train the model using the given data.

    Args:
        model_init (str): pretrained model name. Choose from ``["bert-base-cased",
            "bert-base-uncased", "bert-large-cased", "bert-large-uncased"]``.
        dataset_name (str): the name of the dataset. This is also the dir to save trained model.
        trainset (dict): a fibber dataset.
        testset (dict): a fibber dataset.
        bert_clf_steps (int): steps to train a classifier.
        bert_clf_bs (int): the batch size.
        bert_clf_lr (float): the learning rate.
        bert_clf_optimizer (str): the optimizer name.
        bert_clf_weight_decay (float): the weight decay.
        bert_clf_period_summary (int): the period in steps to write training summary.
        bert_clf_period_val (int): the period in steps to run validation and write validation
            summary.
        bert_clf_period_save (int): the period in steps to save current model.
        bert_clf_val_steps (int): number of batched in each validation.
        bert_clf_enable_sem (bool): whether to use sem to preprocess input.
        bert_clf_sem_word_map (dict): sem word map.
        device (torch.Device): the device to run the model.

    Returns:
        (transformers.BertForSequenceClassification): a torch BERT model.
    """
    if bert_clf_enable_sem:
        model_dir = os.path.join(get_root_dir(), "bert_clf_sem", dataset_name)
    else:
        model_dir = os.path.join(get_root_dir(), "bert_clf", dataset_name)
    ckpt_path = os.path.join(model_dir, model_init + "-%04dk" %
                             (bert_clf_steps // 1000))

    if os.path.exists(ckpt_path):
        logger.info("Load BERT classifier from %s.", ckpt_path)
        model = BertForSequenceClassification.from_pretrained(ckpt_path)
        model.eval()
        model.to(device)
        return model

    num_labels = len(trainset["label_mapping"])

    model = BertForSequenceClassification.from_pretrained(
        resources.get_transformers(model_init), num_labels=num_labels).to(device)
    model.train()

    logger.info("Use %s tokenizer and classifier.", model_init)
    logger.info("Num labels: %s", num_labels)

    summary = SummaryWriter(os.path.join(model_dir, "summary"))

    if bert_clf_enable_sem:
        trainset = sem_transform_dataset(trainset, bert_clf_sem_word_map)
        testset = sem_transform_dataset(testset, bert_clf_sem_word_map)

    dataloader = torch.utils.data.DataLoader(
        DatasetForBert(trainset, model_init, bert_clf_bs), batch_size=None, num_workers=2)

    dataloader_val = torch.utils.data.DataLoader(
        DatasetForBert(testset, model_init, bert_clf_bs), batch_size=None, num_workers=1)
    dataloader_val_iter = iter(dataloader_val)

    params = model.parameters()

    opt, sche = get_optimizer(
        bert_clf_optimizer, bert_clf_lr, bert_clf_weight_decay, bert_clf_steps, params)

    global_step = 0
    correct_train, count_train = 0, 0
    for seq, mask, tok_type, label in tqdm.tqdm(dataloader, total=bert_clf_steps):
        global_step += 1
        seq = seq.to(device)
        mask = mask.to(device)
        tok_type = tok_type.to(device)
        label = label.to(device)

        outputs = model(seq, mask, tok_type, labels=label)
        loss, logits = outputs[:2]

        count_train += seq.size(0)
        correct_train += (logits.argmax(dim=1).eq(label)
                          .float().sum().detach().cpu().numpy())

        opt.zero_grad()
        loss.backward()
        opt.step()
        sche.step()

        if global_step % bert_clf_period_summary == 0:
            summary.add_scalar("clf_train/loss", loss, global_step)
            summary.add_scalar("clf_train/error_rate", 1 - correct_train / count_train,
                               global_step)
            correct_train, count_train = 0, 0

        if global_step % bert_clf_period_val == 0:
            run_evaluate(model, dataloader_val_iter,
                         bert_clf_val_steps, summary, global_step, device)

        if global_step % bert_clf_period_save == 0 or global_step == bert_clf_steps:
            ckpt_path = os.path.join(model_dir, model_init + "-%04dk" % (global_step // 1000))
            model.save_pretrained(ckpt_path)
            logger.info("BERT classifier saved at %s.", ckpt_path)

        if global_step >= bert_clf_steps:
            break
    model.eval()
    return model


class BertClassifier(ClassifierBase):
    """BERT classifier prediction on paraphrase_list.

    This metric is special, it does not compare the original and paraphrased sentence.
    Instead, it outputs the classifier prediction on paraphrase_list. So we should not compute
    mean or std on this metric.

    Args:
        dataset_name (str): the name of the dataset.
        trainset (dict): a fibber dataset.
        testset (dict): a fibber dataset.
        bert_gpu_id (int): the gpu id for BERT model. Set -1 to use CPU.
        bert_clf_steps (int): steps to train a classifier.
        bert_clf_bs (int): the batch size.
        bert_clf_lr (float): the learning rate.
        bert_clf_optimizer (str): the optimizer name.
        bert_clf_weight_decay (float): the weight decay in the optimizer.
        bert_clf_period_summary (int): the period in steps to write training summary.
        bert_clf_period_val (int): the period in steps to run validation and write validation
            summary.
        bert_clf_period_save (int): the period in steps to save current model.
        bert_clf_val_steps (int): number of batched in each validation.
    """

    def __init__(self, dataset_name, trainset, testset, bert_gpu_id=-1,
                 bert_clf_steps=20000, bert_clf_bs=32, bert_clf_lr=0.00002,
                 bert_clf_optimizer="adamw", bert_clf_weight_decay=0.001,
                 bert_clf_period_summary=100, bert_clf_period_val=500,
                 bert_clf_period_save=5000, bert_clf_val_steps=10,
                 bert_clf_enable_sem=False, bert_clf_enable_lmag=False, **kargs):
        super(BertClassifier, self).__init__()

        if trainset["cased"]:
            model_init = "bert-base-cased"
            logger.info(
                "Use cased model in BERT classifier prediction metric.")
        else:
            model_init = "bert-base-uncased"
            logger.info(
                "Use uncased model in BERT classifier prediction metric.")

        self._tokenizer = BertTokenizerFast.from_pretrained(
            resources.get_transformers(model_init), do_lower_case="uncased" in model_init)

        if bert_gpu_id == -1:
            logger.warning("BERT metric is running on CPU.")
            self._device = torch.device("cpu")
        else:
            logger.info("BERT metric is running on GPU %d.", bert_gpu_id)
            self._device = torch.device("cuda:%d" % bert_gpu_id)

        self._enable_sem = bert_clf_enable_sem
        if bert_clf_enable_sem:
            self._sem_word_map = load_or_build_sem_wordmap(dataset_name, trainset, self._device)
        else:
            self._sem_word_map = None

        self._enable_lmag = bert_clf_enable_lmag
        if bert_clf_enable_lmag:
            from fibber.metrics.bert_lm_utils import get_lm
            _, self._lm = get_lm("finetune", dataset_name, trainset, self._device)
            self._lm = self._lm.eval().to(self._device)
            self._lmag_repeat = 10

        self._model_init = model_init
        self._dataset_name = dataset_name
        self._model = load_or_train_bert_clf(
            model_init=model_init,
            dataset_name=dataset_name,
            trainset=trainset,
            testset=testset,
            bert_clf_steps=bert_clf_steps,
            bert_clf_lr=bert_clf_lr,
            bert_clf_bs=bert_clf_bs,
            bert_clf_optimizer=bert_clf_optimizer,
            bert_clf_weight_decay=bert_clf_weight_decay,
            bert_clf_period_summary=bert_clf_period_summary,
            bert_clf_period_val=bert_clf_period_val,
            bert_clf_period_save=bert_clf_period_save,
            bert_clf_val_steps=bert_clf_val_steps,
            bert_clf_enable_sem=bert_clf_enable_sem,
            bert_clf_sem_word_map=self._sem_word_map,
            device=self._device)
        self._fine_tune_sche = None
        self._fine_tune_opt = None

    def predict_dist_batch(self, origin, paraphrase_list, data_record=None,
                           paraphrase_field="text0"):
        """Predict the log-probability distribution over classes for one batch.

        Args:
            origin (str): the original text.
            paraphrase_list (list): a set of paraphrase_list.
            data_record (dict): the corresponding data record of original text.
            paraphrase_field (str): the field name to paraphrase.

        Returns:
            (np.array): a numpy array of size ``(batch_size * num_labels)``.
        """
        if self._enable_sem:
            paraphrase_list = sem_fix_sentences(paraphrase_list, self._sem_word_map)
        if self._enable_lmag:
            context = None
            if "text1" in data_record:
                assert paraphrase_field == "text1"
                context = [data_record["text0"]] * len(paraphrase_list)

            paraphrase_list = lmag_fix_sentences(
                paraphrase_list, context, self._tokenizer, self._lm, self._model, self._device)

        if paraphrase_field == "text0":
            batch_input = self._tokenizer(text=paraphrase_list, padding=True)
        else:
            assert paraphrase_field == "text1"
            batch_input = self._tokenizer(text=[data_record["text0"]] * len(paraphrase_list),
                                          text_pair=paraphrase_list,
                                          padding=True)
        with torch.no_grad():
            logits = self._model(
                input_ids=torch.tensor(batch_input["input_ids"]).to(self._device),
                token_type_ids=torch.tensor(batch_input["token_type_ids"]).to(
                    self._device),
                attention_mask=torch.tensor(batch_input["attention_mask"]).to(self._device)
            )[0]
            if self._enable_lmag:
                res = F.log_softmax(
                    torch.sum(
                        F.one_hot(
                            logits.view(len(paraphrase_list) // self._lmag_repeat,
                                        self._lmag_repeat, -1).argmax(dim=-1),
                            logits.size(-1)),
                        dim=1).float(),
                    dim=1).detach().cpu().numpy()
            else:
                res = F.log_softmax(logits, dim=1).detach().cpu().numpy()
        return res

    def predict_dist_example(self, origin, paraphrase, data_record=None, paraphrase_field="text0"):
        """Predict the log-probability distribution over classes for one example.

        Args:
            origin (str): the original text.
            paraphrase (list): a set of paraphrase_list.
            data_record (dict): the corresponding data record of original text.
            paraphrase_field (str): the field name to paraphrase.

        Returns:
            (np.array): a numpy array of size ``(num_labels)``.
        """
        return self.predict_dist_batch(origin, [paraphrase], data_record, paraphrase_field)[0]

    def robust_tune_init(self, bert_clf_optimizer, bert_clf_lr, bert_clf_weight_decay,
                         bert_clf_steps, **kwargs):
        if self._fine_tune_sche is not None or self._fine_tune_opt is not None:
            logger.error("fine tuning has been initialized.")
            raise RuntimeError("fine tuning has been initialized.")

        params = self._model.parameters()
        self._fine_tune_opt, self._fine_tune_sche = get_optimizer(
            bert_clf_optimizer, bert_clf_lr, bert_clf_weight_decay, bert_clf_steps, params)

    def robust_tune_step(self, data_record_list):
        if self._fine_tune_sche is None or self._fine_tune_opt is None:
            logger.error("fine tuning not initialized.")
            raise RuntimeError("fine tuning not initialized.")

        self._model.train()
        text0_list = []
        text1_list = []
        labels = []
        for data_record in data_record_list:
            if "text0" in data_record:
                text0_list.append(data_record["text0"])
            if "text1" in data_record:
                text1_list.append(data_record["text1"])
            labels.append(data_record["label"])

        if len(text1_list) == 0:
            text1_list = None
        elif len(text1_list) != len(text0_list):
            raise RuntimeError("data records are not consistent.")

        batch_input = self._tokenizer(text=text0_list, text_pair=text1_list, padding=True)

        loss, logits = self._model(
            input_ids=torch.tensor(batch_input["input_ids"]).to(self._device),
            token_type_ids=torch.tensor(batch_input["token_type_ids"]).to(self._device),
            attention_mask=torch.tensor(batch_input["attention_mask"]).to(self._device),
            labels=torch.tensor(labels).to(self._device)
        )[:2]

        self._fine_tune_opt.zero_grad()
        loss.backward()
        self._fine_tune_opt.step()
        self._fine_tune_sche.step()

        self._model.eval()
        return logits.argmax(axis=1).detach().cpu().numpy(), float(loss.detach().cpu().numpy())

    def load_robust_tuned_model(self, desc, step):
        model_dir = os.path.join(get_root_dir(), "bert_clf", self._dataset_name, desc)
        ckpt_path = os.path.join(model_dir, self._model_init + "-%04dk" % (step // 1000))
        self._model = BertForSequenceClassification.from_pretrained(ckpt_path)
        self._model.eval()
        self._model.to(self._device)
        logger.info("Load BERT classifier from %s.", ckpt_path)

    def save_robust_tuned_model(self, desc, step):
        model_dir = os.path.join(get_root_dir(), "bert_clf", self._dataset_name, desc)
        ckpt_path = os.path.join(model_dir, self._model_init + "-%04dk" % (step // 1000))
        self._model.save_pretrained(ckpt_path)
        logger.info("BERT classifier saved at %s.", ckpt_path)
