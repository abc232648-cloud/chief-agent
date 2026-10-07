"""Application-owned binding of shared reporting mechanics to the Farm provider."""
from dataclasses import dataclass
from integrations import internal_reporting
from domains.farming.report_snapshot import snapshot


@dataclass(frozen=True)
class ReportServices:
    catalog: object
    snapshot_provider: object = snapshot

    def run(self,store,registry,principal,body):
        return internal_reporting.run(store,registry,principal,body,
            catalog=self.catalog,snapshot_provider=self.snapshot_provider)

    def history(self,store,registry,principal):
        return internal_reporting.history(store,registry,principal,catalog=self.catalog)
