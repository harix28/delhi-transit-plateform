import sys,sqlite3
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pandas as pd
from src.config import ROOT
base=ROOT/"data/raw/gtfs";out=ROOT/"data/processed/schedule.sqlite"
with sqlite3.connect(out) as con:
 for name in ["trips","calendar"]:
  pd.read_csv(base/(name+".txt"),dtype=str).to_sql(name,con,if_exists="replace",index=False)
 first=True
 for df in pd.read_csv(base/"stop_times.txt",dtype={"trip_id":str,"stop_id":str},chunksize=200000):
  for name in ["arrival_time","departure_time"]:
   parts=df[name].str.split(":",expand=True).astype(int)
   df[name]=parts[0]*3600+parts[1]*60+parts[2]
  df[["trip_id","stop_id","stop_sequence","arrival_time","departure_time"]].to_sql("times",con,if_exists="replace" if first else "append",index=False)
  first=False
 con.execute("CREATE INDEX IF NOT EXISTS times_stop ON times(stop_id,departure_time)")
 con.execute("CREATE INDEX IF NOT EXISTS times_trip ON times(trip_id,stop_id,stop_sequence)")
 con.execute("CREATE INDEX IF NOT EXISTS trips_id ON trips(trip_id)")
 if (base/"calendar_dates.txt").exists():
  pd.read_csv(base/"calendar_dates.txt",dtype=str).to_sql("calendar_dates",con,if_exists="replace",index=False)
print("Schedule index ready",flush=True)
