from domains.contracts import AgentDefinition, DomainDefinition


class AgentRegistry:
    def __init__(self):
        self.domains = {}
        self.agents = {}

    def register(self, domain: DomainDefinition, agent: AgentDefinition):
        if domain.id in self.domains or agent.id in self.agents:
            raise ValueError('Duplicate domain or agent registration.')
        if agent.domain != domain.id:
            raise ValueError('Agent belongs to a different domain.')
        if any(not capability.startswith(domain.id+'.') for capability in agent.capabilities):
            raise ValueError('Agent capabilities must belong to its own domain.')
        names=[action.name for action in domain.actions]
        if len(names)!=len(set(names)):
            raise ValueError('Duplicate action registration.')
        for action in domain.actions:
            if not action.capability.startswith(domain.id+'.'):
                raise ValueError('Action capability must belong to its domain.')
        self.domains[domain.id]=domain
        self.agents[agent.id]=agent

    def resolve(self, domain_id):
        if domain_id not in self.domains:
            raise ValueError('Unknown domain.')
        agents=[a for a in self.agents.values() if a.domain==domain_id]
        if len(agents)!=1:
            raise ValueError('This domain requires explicit agent selection.')
        return self.domains[domain_id], agents[0]

    def describe(self):
        return [{'id':d.id,'label':d.label,'description':d.description,'pages':d.pages,
                 'actions':[{'name':a.name,'label':a.label,'fields':a.fields} for a in d.actions]}
                for d in self.domains.values()]
