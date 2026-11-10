import pytest
from acca_stats.core import wilson, upsert, parse_match, summary

def test_wilson_and_remakes():
    assert wilson(0,0) == 0
    assert wilson(18,19) > wilson(1,1)
    assert summary([{"RESULTADO":"REMAKE"},{"RESULTADO":"VICTORIA"}],lambda x:"ACCA")[0][1] == 1

def test_upsert_is_idempotent():
    assert upsert([["LA2_1","old"]],[["LA2_1","new"]]) == [["LA2_1","new"]]

def fake_match(count=5):
    ps = []
    for i in range(10):
        ps.append({"teamId":100 if i<5 else 200,"puuid":f"p{i}","win":i<5,"championName":"Ahri"})
    return {"metadata":{"matchId":"LA2_123"},"info":{"participants":ps}}, {f"p{i}":f"J{i}" for i in range(count)}

def test_requires_five_members():
    data,members = fake_match(4)
    with pytest.raises(ValueError,match="cinco"):
        parse_match(data,members)

def test_ten_participants_and_reprocessing():
    data,members = fake_match()
    match,players = parse_match(data,members)
    assert match[7] == 5
    assert len(players) == 10
    assert sum(p[4]=="SI" for p in players) == 5
