"""Farm-owned aggregate journal metadata; never quantities or private text."""
from .journal_queries import ACTIVE
from operations.time_integrity import utc_now, utc_text


def snapshot(con):
    # Count all revisions separately from effective entries. Corrections do not
    # silently erase history or become additional stock/production quantities.
    row = con.execute("SELECT count(*), coalesce(sum("+ACTIVE+"),0), "
        "coalesce(sum(json_extract(d.data_json,'$.payload.corrects') IS NOT NULL),0), coalesce(max(d.id),0) "
        "FROM domain_records d WHERE d.domain='farming' AND d.kind='poultry_journal_v1'").fetchone()
    counts = dict(zip(('journal_entries','current_entries','correction_entries'),map(int,row[:3])))
    if any(not 0 <= v <= 1_000_000_000 for v in counts.values()):
        raise ValueError('Record count exceeds the report contract limit.')
    return {'source_contract':'farm.journal-counts.v1', 'as_of':utc_text(utc_now()),
            'counts':counts, 'meaning':'record-counts-not-production-or-stock',
            '_source_cutoff_id':int(row[3])}
