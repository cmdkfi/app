"""One-time readability migration; current builder already produces these strings."""
from pathlib import Path
import json,time
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
D=Path(__file__).resolve().parents[1]/'data';start=time.time();path=D/'retrieval.parquet';pf=pq.ParquetFile(path);tmp=D/'retrieval_summary_tmp.parquet';changed=0
with pq.ParquetWriter(tmp,pf.schema_arrow,compression='zstd') as writer:
    for batch in pf.iter_batches(batch_size=100000):
        table=pa.Table.from_batches([batch]);i=table.schema.get_field_index('summary');before=table.column(i);after=before
        for old,new in [('LTV nan%','LTV unknown'),('price +nan%','price unknown'),('gap +nan%','gap unknown'),('nan% of current collateral','unknown fraction of current collateral')]:after=pc.replace_substring(after,old,new)
        changed+=pc.sum(pc.not_equal(before,after)).as_py();table=table.set_column(i,table.schema.field(i),after);writer.write_table(table)
tmp.replace(path);status=json.loads((D/'step3_build_status.json').read_text());status['bytes']=path.stat().st_size;(D/'step3_build_status.json').write_text(json.dumps(status,indent=2)+'\n');result=dict(changed_summaries=changed,elapsed_s=time.time()-start,only_summary_strings_changed=True);(D/'step3_summary_migration.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
