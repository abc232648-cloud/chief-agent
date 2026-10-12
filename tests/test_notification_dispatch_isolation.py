from worker.runner import _dispatch_push_once


def test_job_transport_exception_does_not_starve_owner_staff_transport():
    calls=[]

    def job(_store):
        calls.append('job')
        raise RuntimeError('synthetic private Job transport failure')

    def devices(_store):
        calls.append('owner-staff')

    _dispatch_push_once(object(),job_dispatch=job,device_dispatch=devices)
    assert calls==['job','owner-staff']


def test_owner_staff_transport_exception_does_not_escape_delivery_tick():
    calls=[]

    def job(_store):
        calls.append('job')

    def devices(_store):
        calls.append('owner-staff')
        raise RuntimeError('synthetic private device transport failure')

    _dispatch_push_once(object(),job_dispatch=job,device_dispatch=devices)
    assert calls==['job','owner-staff']
