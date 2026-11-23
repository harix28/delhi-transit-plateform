import sqlite3
from datetime import timedelta

class Schedule:
 def __init__(self,path):self.path=path
 def next_trip(self,route,board,alight,depart,walk_min):
  reached=depart+timedelta(minutes=walk_min)
  candidates=[]
  with sqlite3.connect(self.path) as con:
   has_exceptions=bool(con.execute("SELECT 1 FROM sqlite_master WHERE name='calendar_dates'").fetchone())
   for offset in [-1,0,1]:
    date=reached.date()+timedelta(days=offset);stamp=date.strftime("%Y%m%d");weekday=date.strftime("%A").lower()
    services={r[0] for r in con.execute(f'SELECT service_id FROM calendar WHERE start_date<=? AND end_date>=? AND "{weekday}"=?',(stamp,stamp,'1'))}
    if has_exceptions:
     for service,kind in con.execute("SELECT service_id,exception_type FROM calendar_dates WHERE date=?",(stamp,)):
      if kind=='1':services.add(service)
      else:services.discard(service)
    if not services:continue
    midnight=depart.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=(date-depart.date()).days)
    threshold=(reached-midnight).total_seconds()
    placeholders=','.join('?' for _ in services)
    sql=f"SELECT a.departure_time,b.arrival_time,t.trip_id FROM times a JOIN trips t ON t.trip_id=a.trip_id JOIN times b ON b.trip_id=a.trip_id WHERE a.stop_id=? AND b.stop_id=? AND b.stop_sequence>a.stop_sequence AND t.route_id=? AND t.service_id IN ({placeholders}) AND a.departure_time>=? AND a.departure_time<=? AND b.arrival_time>=a.departure_time ORDER BY a.departure_time LIMIT 1"
    row=con.execute(sql,(str(board),str(alight),str(route),*sorted(services),threshold,threshold+7200)).fetchone()
    if row:
     leave=midnight+timedelta(seconds=row[0]);arrive=midnight+timedelta(seconds=row[1])
     candidates.append(dict(wait=(leave-reached).total_seconds()/60,ride=(arrive-leave).total_seconds()/60,departure=leave.isoformat(),arrival=arrive.isoformat(),trip_id=row[2]))
  return min(candidates,key=lambda x:x['departure']) if candidates else None
