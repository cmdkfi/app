"""Retain a classifier only after all acceptance gates and validation pass."""
from pathlib import Path
import json,hashlib,time
D=Path(__file__).resolve().parents[1]/'data'
def main():
    decision=json.loads((D/'step4_decision.json').read_text());validation=json.loads((D/'step4_validation.json').read_text());assert validation['all_passed'];fit=json.loads((D/'step4_fit_status.json').read_text());candidate=D/'step4_candidate.joblib';final=D/'risk_model.joblib'
    if decision['keep']:
        if final.exists():assert hashlib.sha256(final.read_bytes()).hexdigest()==fit['model_sha256'],'Do not overwrite an unrelated retained model'
        if candidate.exists():candidate.replace(final)
        assert final.exists();result=dict(kept=True,path='data/risk_model.joblib',bytes=final.stat().st_size,sha256=hashlib.sha256(final.read_bytes()).hexdigest())
    else:
        if candidate.exists():
            assert hashlib.sha256(candidate.read_bytes()).hexdigest()==fit['model_sha256'];candidate.unlink()
        assert not final.exists(),'Unexpected retained model contradicts DROP decision'
        result=dict(kept=False,path=None,bytes=None,evaluated_candidate_bytes=fit['candidate_bytes'],reason=decision['failed'])
    result['completed_at']=time.time();(D/'step4_retention.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
if __name__=='__main__':main()
