import pytest
from acca_stats.analytics import archive, unarchive, detailed_rows, timeline_rows, associate_builds, replace_by_match

def fixture():
    ps=[]
    for i in range(10):
        ps.append({"participantId":i+1,"puuid":f"p{i}","teamId":100 if i<5 else 200,
                   "kills":2,"assists":3,"deaths":1,"goldEarned":12000,
                   "championName":"Ahri","teamPosition":"MIDDLE"})
    return {"metadata":{"matchId":"LA2_42"},"info":{"participants":ps,"gameDuration":1800,
            "teams":[{"teamId":100,"win":True,"objectives":{"dragon":{"kills":3}}}]}}

def test_archive_roundtrip_large_unicode():
    payload={"data":"ドラゴン"*20000,"nested":{"events":[1,2,3]}}
    rows=archive("LA2_42","TIMELINE",payload,chunk_size=200)
    assert len(rows)>1
    assert all(len(r[4])<=200 for r in rows)
    assert unarchive(rows)==payload

def test_details_and_teams():
    data=fixture()
    details,teams=detailed_rows(data,{},100)
    assert len(details)==10
    assert details[0][6]==5
    assert teams[0][5]==3

def test_timeline_build_and_replace():
    data=fixture()
    timeline={"info":{"frames":[{"events":[{"type":"ITEM_PURCHASED","timestamp":62000,
                "participantId":1,"itemId":1055},{"type":"CHAMPION_KILL","timestamp":64000,
                "killerId":1,"victimId":6}]}]}}
    events,builds=timeline_rows("LA2_42",timeline)
    assert len(events)==2
    assert associate_builds(builds,data)[0][1]=="p0"
    assert replace_by_match([["LA2_42","old"],["LA2_9","keep"]],[["LA2_42","new"]],"LA2_42")==[["LA2_9","keep"],["LA2_42","new"]]
