"""This metric computes the embedding similarity using SBERT model."""


from nltk import word_tokenize
from nltk.translate import bleu_score

from fibber import log
from fibber.metrics.metric_base import MetricBase

logger = log.setup_custom_logger(__name__)


class RefBleuMetric(MetricBase):
    """This metric computes the bleu score between input and output"""

    def __init__(self, **kargs):
        """Initialize ce model."""
        super(RefBleuMetric, self).__init__()

    def measure_example(self, origin, paraphrase, data_record=None, paraphrase_field="text0"):
        """Compute the 4 gram self bleu

        Args:
            origin (str): original text.
            paraphrase (str): paraphrased text.
            data_record: ignored.
            paraphrase_field: ignored.
        """
        try:
            ref = data_record["ref"]
            if not isinstance(ref, list):
                ref = [ref]
            ref = [word_tokenize(item) for item in ref]
        except BaseException:
            logger.warning("Ref not found in data, Ref Blue is set to 0.")
            return 0
        hypo = word_tokenize(paraphrase)
        chencherry = bleu_score.SmoothingFunction()
        return bleu_score.sentence_bleu(ref, hypo, smoothing_function=chencherry.method1)
