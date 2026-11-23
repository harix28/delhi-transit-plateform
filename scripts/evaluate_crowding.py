"""Chronological holdout of ticket-load estimates, not actual vehicle occupancy."""
import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pandas as pd
from src.config import load_config,resolve
from src.ingest.tickets import load_tickets
from src.models.load import match_stops,build_segment_load
from src.models.crowding import add_crowding

def main():
 cfg=load_config();out=resolve(cfg,"outputs");out.mkdir(parents=True,exist_ok=True)
 tickets=load_tickets(resolve(cfg,"raw")/"tickets",cfg)
 rs=pd.read_parquet(resolve(cfg,"processed")/"route_stops.parquet")
 matched=match_stops(tickets,rs)
 flags=tickets.assign(matched=tickets.ticket_id.isin(matched.ticket_id))
 quality=flags.groupby(["route_short","leg"]).agg(records=("ticket_id","size"),matched=("matched","sum")).reset_index()
 quality["match_rate"]=quality.matched/quality.records;quality.to_csv(out/"route_match_quality.csv",index=False)
 flags.loc[~flags.matched].groupby(["route_short","leg","start_norm","end_norm"]).size().sort_values(ascending=False).head(500).rename("records").to_csv(out/"unmatched_stop_pairs.csv")
 # Fixed temporal split prevents future ticket records leaking into training.
 cutoff=pd.Timestamp("2026-10-25", tz=tickets.ts.dt.tz)
 train=tickets[tickets.ts<cutoff];test=tickets[tickets.ts>=cutoff]
 a=add_crowding(build_segment_load(train,rs,cfg),cfg)
 b=add_crowding(build_segment_load(test,rs,cfg),cfg)
 keys=["route_id","direction_id","seg","day_type","half_hour"]
 merged=a.merge(b,on=keys,suffixes=("_train","_test"))
 reliable=merged[(merged.n_trips_train>=5)&(merged.n_trips_test>=5)]
 metrics={"status":"complete", "population":"tickets with a usable vehicle ID and valid journey fields", "records":len(tickets),"matched_records":len(matched),"match_rate":len(matched)/len(tickets),"cutoff":str(cutoff.date()),"train_records":len(train),"test_records":len(test),"test_bins":len(b),"overlap_bins":len(merged),"evaluated_bins_min_5_trips_each":len(reliable),"target":"held-out ticket-derived segment mean; NOT measured total occupancy"}
 if len(reliable):
  error=reliable.load_est_train-reliable.load_est_test
  metrics.update(mae=float(error.abs().mean()),rmse=float((error.pow(2).mean())**.5),level_agreement=float((reliable.level_train==reliable.level_test).mean()))
 metrics["train_label_distribution_evaluated"]=reliable.level_train.value_counts().to_dict()
 metrics["test_label_distribution_evaluated"]=reliable.level_test.value_counts().to_dict()
 metrics["interpretation_warning"]="Class agreement can be trivial when sparse ticket samples all fall below capacity; it does not demonstrate crowding detection."
 (out/"evaluation.json").write_text(json.dumps(metrics,indent=2),encoding="utf-8")
 reliable.to_csv(out/"holdout_comparison.csv",index=False)
 for name,frame in [("train",a),("holdout",b)]:
  frame.groupby(["day_type","half_hour"]).agg(mean_ticket_load=("load_est","mean"),segment_bins=("seg","size"),observed_trips=("n_trips","sum")).to_csv(out/(name+"_time_patterns.csv"))
 print(json.dumps(metrics,indent=2),flush=True)
if __name__=="__main__":main()
