import logging

from desaymem.core.logging import RedactingFilter, configure_logging


def test_logging_filter_does_not_keep_api_key(caplog):
    configure_logging("INFO")
    logger = logging.getLogger("desaymem.secret-test")
    logger.addFilter(RedactingFilter())
    with caplog.at_level(logging.INFO, logger="desaymem.secret-test"):
        logger.info("using api_key=sk-live-secret-value")
    assert "sk-live-secret-value" not in caplog.text
    assert "api_key=***" in caplog.text or "***" in caplog.text
