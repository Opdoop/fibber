import os
import tempfile

import fasttext
import numpy as np

from fibber import get_root_dir, log
from fibber.metrics.classifier_base import ClassifierBase

logger = log.setup_custom_logger(__name__)


def change_to_fasttext_format(dataset, filename):
    """Change Fibber's dataset to fast text's format and save to a file."""
    with open(filename, "w", encoding="utf8") as f:
        for item in dataset["data"]:
            if "text1" in item:
                logger.error("FastText does not support text1.")
                raise RuntimeError
            print("__label__%d" % item["label"], item["text0"], file=f)


class FasttextClassifier(ClassifierBase):
    """fasttext classifier prediction on paraphrase_list.

    This metric is special, it does not compare the original and paraphrased sentence.
    Instead, it outputs the classifier prediction on paraphrase_list. So we should not compute
    mean or std on this metric.

    Args:
        dataset_name (str): the name of the dataset.
        trainset (dict): a fibber dataset.
        testset (dict): a fibber dataset.
        fasttext_lr (float): learning rate.
        fasttext_epoch (int): epochs to train.
        fasttext_ngram (int): classification feature ngram.
    """

    def __init__(self, dataset_name, trainset, testset,
                 fasttext_lr=1., fasttext_epoch=25, fasttext_ngram=5, **kargs):
        super(FasttextClassifier, self).__init__()
        model_dir = os.path.join(get_root_dir(), "fasttext_clf", dataset_name)
        os.makedirs(model_dir, exist_ok=True)
        model_filename = os.path.join(
            model_dir, "fasttext_model_ngram_%d_epoch_%d.bin" % (fasttext_ngram, fasttext_epoch))

        if os.path.exists(model_filename):
            self._model = fasttext.load_model(model_filename)
            self._n_class = len(trainset["label_mapping"])
        else:
            tmp_dir = tempfile.gettempdir()
            fasttext_train_filename = os.path.join(tmp_dir, "%s.train" % dataset_name)
            fasttext_test_filename = os.path.join(tmp_dir, "%s.test" % dataset_name)
            change_to_fasttext_format(trainset, fasttext_train_filename)
            change_to_fasttext_format(testset, fasttext_test_filename)

            self._model = fasttext.train_supervised(input=fasttext_train_filename,
                                                    lr=fasttext_lr,
                                                    epoch=fasttext_epoch,
                                                    wordNgrams=fasttext_ngram)
            _, precision, recall = self._model.test(fasttext_test_filename, k=1)
            logger.info("Fast Text Precision %f, Recall %f", precision, recall)
            self._model.save_model(model_filename)
            self._n_class = len(trainset["label_mapping"])

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
        labels, probs = self._model.predict(paraphrase, k=self._n_class)
        ret = np.zeros(self._n_class)
        for label, prob in zip(labels, probs):
            idx = int(label[len("__label__"):])
            ret[idx] = prob
        return ret
