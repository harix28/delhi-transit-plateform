"""Historical AC evidence from October tickets; not current fleet verification."""
import sys,zipfile,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pandas as pd
from src.config import ROOT
parts=[];total=0;missing=0
with zipfile.ZipFile(ROOT/"data/raw/tickets/October-Delhi.csv.zip") as archive:
 with archive.open("October-Delhi.csv") as stream:
  for d in pd.read_csv(stream,usecols=["vehicle_number","is_ac"],chunksize=200000):
   total+=len(d);missing+=int(d.vehicle_number.isna().sum())
   d=d.dropna(subset=["vehicle_number","is_ac"])
   d["vehicle_id"]=d.vehicle_number.astype(str).str.upper().str.replace(r"[^A-Z0-9]","",regex=True)
   d["ac"]=d.is_ac.astype(str).str.lower().map({"true":1,"false":0,"1":1,"0":0})
   d=d.dropna(subset=["ac"])
   parts.append(d.groupby("vehicle_id").ac.agg(["sum","count"]))
result=pd.concat(parts).groupby(level=0).sum();result["agreement"]=result[["sum"]].assign(other=result["count"]-result["sum"]).max(axis=1)/result["count"]
result["type"]="unknown"
result.loc[(result["count"]>=20)&(result.agreement>=.98)&(result["sum"]/result["count"]>=.98),"type"]="ac"
result.loc[(result["count"]>=20)&(result.agreement>=.98)&(result["sum"]/result["count"]<=.02),"type"]="non-ac"
result.reset_index().to_parquet(ROOT/"data/processed/vehicle_types.parquet",index=False)
report={"ticket_rows":total,"missing_vehicle_rows":missing,"vehicles":len(result),"classifications":result.type.value_counts().to_dict(),"basis":"October 2026 ticket declarations; at least 20 observations and 98% agreement; not current fleet verification"}
(ROOT/"outputs/vehicle_type_quality.json").write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)
