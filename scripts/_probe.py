import json,os,sys,time,glob,urllib.request,urllib.parse,collections
BASE="https://cocproxy.royaleapi.dev/v1"
KEY=os.environ["COC_API_KEY"]
R="docs/data/"
n=[0]
def get(p):
    for i in range(4):
        try:
            n[0]+=1
            q=urllib.request.Request(BASE+p,headers={"Authorization":"Bearer "+KEY,"Accept":"application/json"})
            return json.load(urllib.request.urlopen(q,timeout=30))
        except urllib.error.HTTPError as e:
            if e.code in(404,403): return None
            if e.code==429 or e.code>=500: time.sleep(1+i)
            else: raise
        except Exception: time.sleep(1+i)
    return None
q=lambda t:urllib.parse.quote(t,safe="")
L=json.load(open(R+"latest.json"));print("data van",L["bijgewerkt"])
P={p["tag"]:p for p in L["players"]}
CL={"#PYQ2RLCQ":"I","#2RGYGLVC0":"II","#2JYY0809P":"III","#2CJY8LJU0":"IV"}
out=collections.Counter();ex=collections.defaultdict(list)
def diff(k,msg):
    out[k]+=1
    if len(ex[k])<6: ex[k].append(msg)
A=json.load(open(R+"archief.json"));RF=json.load(open(R+"roof.json"))["weekenden"]
live=set()
for c,nr in CL.items():
    d=get("/clans/"+q(c));ml={m["tag"] for m in d["memberList"]};live|=ml
    st={p["tag"] for p in L["players"] if p.get("inClan") and p.get("clanTag")==c}
    print(nr,"leden api",len(ml),"site",len(st),"alleen api",sorted(ml-st)[:4],"alleen site",sorted(st-ml)[:4],"warlog",d.get("warWins"),d.get("warTies"),d.get("warLosses"),d.get("warLeague",{}).get("name"),"caplvl",d.get("clanCapital",{}).get("capitalHallLevel"),d.get("clanLevel"))
    wl=get("/clans/"+q(c)+"/warlog?limit=200")["items"]
    ar={w.get("einde"):w for w in A.get(c,[])}
    nieuw=0;ok=0;cwlrij=0
    for w in wl:
        if not w.get("opponent",{}).get("tag"): cwlrij+=1;continue
        a=ar.get(w["endTime"].replace(".000Z",".000Z"))
        if not a: diff("warlog ontbreekt in archief",nr+" "+w["endTime"]+" "+w["opponent"].get("name",""));continue
        if a["s"]!=[w["clan"]["stars"],w["opponent"]["stars"]]: diff("warlog sterren",nr+" "+w["endTime"]+str(a["s"])+str([w["clan"]["stars"],w["opponent"]["stars"]]))
        elif a.get("gebruikt") not in (None,w["clan"].get("attacks")): diff("warlog aanvallen",nr+" "+w["endTime"]+f" {a.get('gebruikt')} {w['clan'].get('attacks')}")
        else: ok+=1
    print(nr,"warlog",len(wl),"ok",ok,"cwl-regels",cwlrij,"archief",len(A.get(c,[])))
    rs=get("/clans/"+q(c)+"/capitalraidseasons?limit=52")
    ok=0
    for s in (rs or {}).get("items",[]):
        k=c+"|"+s["startTime"];r=RF.get(k)
        if not r: diff("raid ontbreekt",nr+" "+s["startTime"]);continue
        v=[("buit","capitalTotalLoot"),("aanvallen","totalAttacks"),("wijken","enemyDistrictsDestroyed"),("raids","raidsCompleted"),("beloning","offensiveReward"),("verdediging","defensiveReward")]
        fout=[f"{a}:{r.get(a)}!={s.get(b)}" for a,b in v if r.get(a)!=s.get(b) and not(a in("beloning","verdediging") and r.get(a) is None)]
        if s.get("state")=="ended" and fout: diff("raid verschil",nr+" "+s["startTime"]+" "+",".join(fout))
        else: ok+=1
    print(nr,"raids api",len((rs or {}).get("items",[])),"ok",ok)
    g=get("/clans/"+q(c)+"/currentwar/leaguegroup")
    if g and g.get("rounds"):
        ok=0;ro=0
        for r in g["rounds"]:
            for wt in r["warTags"]:
                if wt=="#0": continue
                f=R+"cwl/"+wt[1:]+".json"
                if not os.path.exists(f): continue
                o=json.load(open(f));w=get("/clanwarleagues/wars/"+q(wt))
                if not w: continue
                ro+=1
                k={x["tag"]:x for x in o["kanten"]}
                for z in ("clan","opponent"):
                    b=w[z];a=k.get(b["tag"])
                    if not a: diff("cwl kant",wt);continue
                    if a["sterren"]!=b["stars"] or a["aanvallen"]!=b["attacks"]:
                        diff("cwl verschil "+w["state"],f"{nr} {wt} {a['sterren']}/{a['aanvallen']} api {b['stars']}/{b['attacks']}")
                    else: ok+=1
        print(nr,"cwl oorlogen",ro,"kanten ok",ok)
tags=sorted(live)
print("spelers",len(tags),"site inClan",sum(1 for p in L["players"] if p.get("inClan")))
vel=collections.Counter();tot=0
for t in tags:
    a=get("/players/"+q(t))
    if not a: diff("speler niet opgehaald",t);continue
    f=R+"speler/"+t[1:]+".json"
    if not os.path.exists(f): diff("geen spelerbestand",t);continue
    s=json.load(open(f));p=P.get(t,{})
    for k,b in (("warSterren","warStars"),("kapitaal","clanCapitalContributions"),("xp","expLevel"),("donaties","donations"),("ontvangen","donationsReceived")):
        if s.get(k)!=a.get(b): vel[k]+=1;diff("veld "+k,f"{a['name']} {s.get(k)} api {a.get(b)}")
    if p.get("th")!=a["townHallLevel"]: diff("th",f"{a['name']} {p.get('th')} api {a['townHallLevel']}")
    if p.get("league")!=(a.get("leagueTier") or {}).get("name"): vel["league"]+=1;diff("veld league",f"{a['name']} {p.get('league')} api {(a.get('leagueTier') or {}).get('name')}")
    api={}
    for lst in ("heroes","spells","troops","heroEquipment"):
        for x in a.get(lst,[]):
            if x.get("village","home")=="home": api[x["name"]]=x["level"]
    for k in ("helden","dieren","spreuken","troepen","equipment"):
        for x in s.get(k,[]):
            tot+=1
            if api.get(x["naam"])!=x["lvl"]: diff("level "+k,f"{a['name']} {x['naam']} {x['lvl']} api {api.get(x['naam'])}")
    gc=next((z["value"] for z in a.get("achievements",[]) if z["name"]=="Games Champion"),None)
    if p.get("gamesTotaal")!=gc: vel["games"]+=1;diff("veld games",f"{a['name']} {p.get('gamesTotaal')} api {gc}")
print("levels vergeleken",tot,"api-calls",n[0])
print("VERSCHILLEN",dict(out));print(json.dumps(ex,ensure_ascii=False,indent=0)[:6000])
# losse feiten
RK=json.load(open(R+"ranked.json"))
lt=get("/leaguetiers");print("tiers",len(lt["items"]) if lt else None)
