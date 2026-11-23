from datetime import datetime
import pandas as pd
from src.routing.journeys import JourneyPlanner
from src.config import load_config

def test_two_bus_journey_has_ordered_legs_and_buffer():
    p=JourneyPlanner.__new__(JourneyPlanner)
    p.cfg=load_config();p.cfg["journey"]["walk_max_m"]=100
    p.schedule=None;p.speed={};p.city={};p.crowd=None
    p.stops=pd.DataFrame({"stop_id":["a","x","b"],"stop_name":["A","X","B"],"stop_lat":[28.6,28.63,28.66],"stop_lon":[77.2]*3})
    rows=[]
    for route,indices in [("R1",[0,1]),("R2",[1,2])]:
        for seq,index in enumerate(indices):
            row=p.stops.iloc[index].to_dict();row.update(route_id=route,route_short=route,direction_id=0,seq=seq,dist_m=seq*3300)
            rows.append(row)
    p.rs=pd.DataFrame(rows)
    result=p.plan_with_transfer((28.6,77.2),(28.66,77.2),datetime(2026,11,20,8))
    assert len(result)==1
    journey=result[0]
    assert journey["transfers"]==1
    assert [leg["route"] for leg in journey["legs"]]==["R1","R2"]
    assert journey["transfer_stop"]=="X"
    assert journey["total_min"]==round(sum(leg["total_min"] for leg in journey["legs"])+2,1)
    assert p.plan_with_transfer((28.66,77.2),(28.6,77.2),datetime(2026,11,20,8))==[]
