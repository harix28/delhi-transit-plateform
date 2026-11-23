import sqlite3
from datetime import datetime
from src.routing.schedule import Schedule

def test_calendar_and_midnight_service(tmp_path):
 p=tmp_path/'schedule.sqlite'
 with sqlite3.connect(p) as c:
  c.executescript("CREATE TABLE calendar(service_id,start_date,end_date,monday,tuesday,wednesday,thursday,friday,saturday,sunday); CREATE TABLE trips(trip_id,route_id,service_id); CREATE TABLE times(trip_id,stop_id,stop_sequence,departure_time,arrival_time); CREATE TABLE calendar_dates(service_id,date,exception_type);")
  c.execute("INSERT INTO calendar VALUES ('s','20260101','20261231','0','0','0','0','1','0','0')")
  c.execute("INSERT INTO trips VALUES ('t','r','s')")
  c.executemany("INSERT INTO times VALUES (?,?,?,?,?)",[('t','a',0,90000,90000),('t','b',1,91200,91200)])
 s=Schedule(p)
 result=s.next_trip('r','a','b',datetime(2026,11,21,0,50),0)
 assert result['wait']==10 and result['ride']==20
 assert s.next_trip('r','b','a',datetime(2026,11,21,0,50),0) is None
 with sqlite3.connect(p) as c:c.execute("INSERT INTO calendar_dates VALUES ('s','20261120','2')")
 assert s.next_trip('r','a','b',datetime(2026,11,21,0,50),0) is None
