"""Compatibility declarations for current Job execution; no new worker behavior."""
from capabilities.contracts import Capability, Component, ComponentKind, Consumer, Dependency, Maturity, Mode, Node


JOB_TESTS = (
    'tests/test_scrapy_collection.py', 'tests/test_command_processor.py', 'tests/test_worker_planning_context.py',
    'tests/test_worker_policy_gate.py', 'tests/test_browser_worker.py',
    'tests/test_application_generation.py', 'tests/test_draft_evidence.py',
    'tests/test_v20_fact_governance.py', 'tests/test_v21_application_executor.py',
    'tests/test_v22_application_archive.py', 'tests/test_v23_final_submission.py',
    'tests/test_v24_recovery.py', 'tests/test_access_browser.py',
    'tests/test_job_pipeline.py', 'tests/test_job_ranking.py',
    'tests/test_ai_job_analysis.py', 'tests/test_policy.py', 'tests/test_domains.py',
    'tests/test_v19_cv_library.py', 'tests/test_source_discovery.py', 'tests/test_sources.py',
    'tests/test_source_config.py', 'tests/test_source_runtime_limits.py',
    'tests/test_site_access.py', 'tests/test_playwright_reader.py',
    'tests/test_v27_hardening.py', 'tests/test_v28_rehearsal.py',
)


def components():
    return (Component('jobs.worker', ComponentKind.SERVICE,
                      dependencies=tuple(Dependency(Node('component', name)) for name in
                                         ('chief.database', 'chief.policy', 'chief.gateway', 'chief.browser')),
                      tests=JOB_TESTS),)


def definitions():
    return (Capability(
        id='jobs.execute', owner='jobs', version='1.0.0',
        description='Job command processor with optional configured Scrapy source collectors; preserves current safety gates.',
        maturity=Maturity.LIMITED, mode=Mode.LEGACY_CONTROLLED,
        dependencies=(Dependency(Node('capability', 'chief.domain_dispatch')),
                      Dependency(Node('component', 'jobs.worker'))),
        consumers=(Consumer('jobs-worker', True, JOB_TESTS),), permissions=('jobs.execute',),
        frameworks=('Existing Job Policy Gate, source/session restrictions and confirmed-fact rules.',),
        data_access=('Job candidate facts, CVs, applications, sources and provenance under current access rules.',),
        models=('Existing configured Qwen/Groq primary and Mistral fallback; no routing change.',),
        tests=JOB_TESTS, health_dependencies=('chief.database', 'chief.gateway', 'chief.browser'),
        inputs='Current command ID/instruction or separately approved action ID.',
        outputs='Current processor result and persisted command/action state.',
        side_effects=('Current guarded Job operations, including separately approved external mutations.',),
        failure_behavior='Current policy blocks, safe provider failure and ambiguous-outcome review; no blind replay.',
        overrides=('Existing policy and approval boundaries cannot be relaxed by registry metadata.',),
        audit=('Current command, action, application snapshot/event and audit records.',)),)
