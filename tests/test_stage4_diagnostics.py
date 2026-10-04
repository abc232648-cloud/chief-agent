import logging
from operations.diagnostics import configure


def test_logs_are_bounded_and_payload_exception_values_absent(tmp_path,caplog):
    previous_propagation=logging.getLogger('chief.http').propagate
    logger,handler=configure(tmp_path,'dashboard');handler.maxBytes=160
    try:
        for _ in range(40):logger.warning('APPLICATION_ERROR correlation=%s','a'*32)
        logger.warning('api_key=synthetic-secret')
        try:raise RuntimeError('synthetic-private-exception')
        except RuntimeError:logger.exception('APPLICATION_ERROR correlation=%s','a'*32)
        files=list((tmp_path/'diagnostics').iterdir())
        assert len(files)==4 and all(p.stat().st_size<=160 for p in files)
        text=''.join(p.read_text() for p in files)
        assert 'synthetic' not in text and 'APPLICATION_ERROR' in text
        assert 'synthetic' not in caplog.text
        assert logger.propagate is False
    finally:
        logger.removeHandler(handler);handler.close();logger.propagate=previous_propagation
