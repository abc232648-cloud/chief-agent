from domains.contracts import AgentDefinition, DomainDefinition


def make_processor(store,gateway):
    from .ledger_adapter import JobCommandProcessor as CommandProcessor
    from .policy_adapter import JobComparisonGate
    return CommandProcessor(store,gateway,gate=JobComparisonGate(store))


def definition():
    return (DomainDefinition('jobs','Job Agent','Job research, candidate evidence and reviewed applications.', processor_factory=make_processor,
            pages=(('jobOverview','Overview'),('jobs','Opportunities'),('applications','Applications'),('applicationArchive','Application archive'),('sources','Sources & sessions'),('cvs','CV library'),('profiles','Profile links'),('facts','Candidate facts'))),
            AgentDefinition('jobs-worker','jobs',frozenset({'jobs.execute'})))
