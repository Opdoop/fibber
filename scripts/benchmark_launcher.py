import argparse
import copy
import subprocess

COMMON_CONFIG = {
    "--subsample_testset": 1000,
    "--num_paraphrases_per_text": 50,
    "--robust_tuning": "0",
    # ignored when robut_tuning is 0 and load_robust_tuned_clf is not set
    "--robust_tuning_steps": 5000,
}

DEFENSE_CONFIG = {
    "none": {},
    "sem": {
        "--bert_clf_enable_sem": "1"
    },
    "lmag": {
        "--bert_clf_enable_lmag": "1"
    }
}

GPU_CONFIG = {
    "0": {
        "--bert_gpu_id": 0,
        "--use_gpu_id": 0,
        "--gpt2_gpu_id": 0,
        "--strategy_gpu_id": 0,
        "--ce_gpu_id": 0
    },
    "1": {
        "--bert_gpu_id": 1,
        "--use_gpu_id": 1,
        "--gpt2_gpu_id": 1,
        "--ce_gpu_id": 1,
        "--strategy_gpu_id": 1,
    }
}

DATASET_CONFIG = {
    "ag_no_title": {
        "--dataset": "ag_no_title",
        "--output_dir": "exp-ag_no_title",
        "--bert_clf_steps": 20000
    },
    "mr": {
        "--dataset": "mr",
        "--output_dir": "exp-mr",
        "--bert_clf_steps": 5000
    },
    "imdb": {
        "--dataset": "imdb",
        "--output_dir": "exp-imdb",
        "--bert_clf_steps": 5000
    },
    "yelp": {
        "--dataset": "yelp",
        "--output_dir": "exp-yelp",
        "--bert_clf_steps": 20000
    },
    "snli": {
        "--dataset": "snli",
        "--output_dir": "exp-snli",
        "--bert_clf_steps": 20000
    },
    "mnli": {
        "--dataset": "mnli",
        "--output_dir": "exp-mnli",
        "--bert_clf_steps": 20000
    },
    "mnli_mis": {
        "--dataset": "mnli_mis",
        "--output_dir": "exp-mnli_mis",
        "--bert_clf_steps": 20000
    },
    "sst2": {
        "--dataset": "sst2",
        "--output_dir": "exp-sst2",
        "--bert_clf_steps": 20000
    },
    "qnli": {
        "--dataset": "qnli",
        "--output_dir": "exp-qnli",
        "--bert_clf_steps": 20000
    },
}

STRATEGY_CONFIG = {
    "identity": {
        "--strategy": "IdentityStrategy"
    },
    "random": {
        "--strategy": "RandomStrategy"
    },
    "textfooler": {
        "--strategy": "TextAttackStrategy",
        "--ta_recipe": "TextFoolerJin2019",
        "--robust_tune_num_attack_per_step": 20
    },
    "pso": {
        "--strategy": "OpenAttackStrategy",
        "--ta_recipe": "PSOAttacker",
        "--robust_tune_num_attack_per_step": 5
    },
    "bertattack": {
        "--strategy": "OpenAttackStrategy",
        "--oa_recipe": "BERTAttacker",
        "--robust_tune_num_attack_per_step": 5
    },
    "bae": {
        "--strategy": "OpenAttackStrategy",
        "--oa_recipe": "BAEAttacker",
        "--robust_tune_num_attack_per_step": 5
    },
    "scpn": {
        "--strategy": "OpenAttackStrategy",
        "--oa_recipe": "SCPNAttacker",
        "--robust_tune_num_attack_per_step": 5
    },
    "gsa": {
        "--strategy": "TextAttackStrategy",
        "--ta_recipe": "Kuleshov2017",
        "--robust_tune_num_attack_per_step": 5
    },
    "pwws": {
        "--strategy": "TextAttackStrategy",
        "--ta_recipe": "PWWSRen2019",
        "--robust_tune_num_attack_per_step": 5
    },
    "asrs": {
        "--strategy": "ASRSStrategy",
        "--asrs_enforcing_dist": "wpe",
        "--asrs_wpe_threshold": 1.0,
        "--asrs_wpe_weight": 1000,
        "--asrs_sim_threshold": 0.95,
        "--asrs_sim_weight": 500,
        "--asrs_ppl_weight": 5,
        "--asrs_sampling_steps": 200,
        "--asrs_burnin_steps": 100,
        "--asrs_clf_weight": 3,
        "--asrs_window_size": 3,
        "--asrs_accept_criteria": "joint_weighted_criteria",
        "--asrs_burnin_enforcing_schedule": "1",
        "--asrs_burnin_criteria_schedule": "1",
        "--asrs_seed_option": "dynamic_len",
        "--asrs_split_sentence": "1",
        "--asrs_lm_option": "finetune",
        "--asrs_stanza_port": 9001,
        "--asrs_sim_metric": "CESimilarityMetric",
        "--robust_tune_num_attack_per_step": 5
    },
    "fu": {
        "--strategy": "FudgeStrategy",
    },
    "asrs-nli": {
        "--asrs_sim_weight": 100,
        "--asrs_ppl_weight": 3,
        "--asrs_clf_weight": 3,
    },
    "asrs-u": {
        "--asrs_sim_metric": "USESimilarityMetric",
        "--best_adv_metric_name": "USESimilarityMetric"
    },
    "asrs-u-nli": {
        "--asrs_sim_weight": 100,
        "--asrs_ppl_weight": 3,
        "--asrs_clf_weight": 3,
        "--asrs_sim_metric": "USESimilarityMetric",
        "--best_adv_metric_name": "USESimilarityMetric"
    }
}


def to_command(args):
    ret = []
    for k, v in args.items():
        ret.append(k)
        ret.append(str(v))

    return ret


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("--gpu", choices=list(GPU_CONFIG.keys()), default="0")
    parser.add_argument("--dataset", choices=list(DATASET_CONFIG.keys()) + ["all"], default="all")
    parser.add_argument("--strategy", choices=list(STRATEGY_CONFIG.keys()) + ["all"],
                        default="all")
    parser.add_argument("--robust_desc", type=str, default=None)
    parser.add_argument("--robust_tuning", type=str, default="0")
    parser.add_argument("--defense", type=str, default="none")

    args = parser.parse_args()

    if args.robust_tuning == "1":
        COMMON_CONFIG["--robust_tuning"] = "1"

    if args.dataset == "all":
        dataset_list = list(DATASET_CONFIG.keys())
    else:
        dataset_list = [args.dataset]

    if args.strategy == "all":
        strategy_list = list(STRATEGY_CONFIG.keys())
    else:
        strategy_list = [args.strategy]

    for dataset in dataset_list:
        for strategy in strategy_list:
            command = ["python3", "-m", "fibber.benchmark.benchmark"]
            if args.robust_desc is not None:
                command += to_command({"--load_robust_tuned_clf": args.robust_desc})
            command += to_command(COMMON_CONFIG)
            command += to_command(GPU_CONFIG[args.gpu])
            command += to_command(DATASET_CONFIG[dataset])
            command += to_command(DEFENSE_CONFIG[args.defense])
            if strategy[:4] == "asrs":
                strategy_config_tmp = copy.copy(STRATEGY_CONFIG["asrs"])
                if strategy != "asrs":
                    for k, v in STRATEGY_CONFIG[strategy].items():
                        strategy_config_tmp[k] = v
                command += to_command(strategy_config_tmp)
            else:
                command += to_command(STRATEGY_CONFIG[strategy])
            subprocess.call(command)


if __name__ == "__main__":
    main()
