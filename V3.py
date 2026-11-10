
import requests
import math
from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)

API_URL = "https://api.football-data.org/v4"


# =========================
# API
# =========================

def get_data(endpoint, token, params=None):
    try:
        r = requests.get(
            API_URL + endpoint,
            headers={"X-Auth-Token": token},
            params=params,
            timeout=25
        )

        if r.status_code == 401:
            return {"error": "API KEY غير صحيح"}
        if r.status_code == 403:
            return {"error": "هاد البيانات غير متاحة لحسابك"}
        if r.status_code == 429:
            return {"error": "تجاوزت حد الطلبات، حاول لاحقاً"}

        r.raise_for_status()
        return r.json()

    except requests.exceptions.Timeout:
        return {"error": "انتهى وقت الاتصال"}
    except requests.exceptions.ConnectionError:
        return {"error": "تحقق من الإنترنت"}
    except Exception as e:
        return {"error": str(e)}


# =========================
# STATISTICS
# =========================

def weighted_average(values):
    if not values:
        return 0

    weights = list(range(1, len(values) + 1))
    return sum(v * w for v, w in zip(values, weights)) / sum(weights)


def team_statistics(matches, team_id):
    gf, ga = [], []
    home_gf, home_ga = [], []
    away_gf, away_ga = [], []

    wins = draws = losses = 0

    for match in matches:
        home = match.get("homeTeam", {})
        away = match.get("awayTeam", {})
        score = match.get("score", {}).get("fullTime", {})

        hg = score.get("home")
        ag = score.get("away")

        if hg is None or ag is None:
            continue

        if home.get("id") == team_id:
            scored, conceded = hg, ag
            home_gf.append(hg)
            home_ga.append(ag)

        elif away.get("id") == team_id:
            scored, conceded = ag, hg
            away_gf.append(ag)
            away_ga.append(hg)
        else:
            continue

        gf.append(scored)
        ga.append(conceded)

        if scored > conceded:
            wins += 1
        elif scored == conceded:
            draws += 1
        else:
            losses += 1

    if not gf:
        return None

    overall_gf = weighted_average(gf)
    overall_ga = weighted_average(ga)

    def blend(split, overall):
        if len(split) < 3:
            return overall
        return weighted_average(split) * 0.65 + overall * 0.35

    return {
        "played": len(gf),
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "gf": overall_gf,
        "ga": overall_ga,
        "home_gf": blend(home_gf, overall_gf),
        "home_ga": blend(home_ga, overall_ga),
        "away_gf": blend(away_gf, overall_gf),
        "away_ga": blend(away_ga, overall_ga)
    }


# =========================
# POISSON
# =========================

def poisson(lam, goals):
    return math.exp(-lam) * lam ** goals / math.factorial(goals)


def calculate_prediction(home, away):
    xg_home = (
        home["home_gf"] * 0.55
        + away["away_ga"] * 0.45
    )

    xg_away = (
        away["away_gf"] * 0.55
        + home["home_ga"] * 0.45
    )

    xg_home *= 1.06

    xg_home = max(0.10, min(xg_home, 4.50))
    xg_away = max(0.10, min(xg_away, 4.50))

    p_home = p_draw = p_away = 0
    over15 = over25 = over35 = 0
    btts = 0
    scores = []

    for h in range(11):
        for a in range(11):
            p = poisson(xg_home, h) * poisson(xg_away, a)
            scores.append([h, a, p])

            if h > a:
                p_home += p
            elif h == a:
                p_draw += p
            else:
                p_away += p

            total = h + a

            if total >= 2:
                over15 += p
            if total >= 3:
                over25 += p
            if total >= 4:
                over35 += p
            if h > 0 and a > 0:
                btts += p

    total = p_home + p_draw + p_away

    scores.sort(key=lambda x: x[2], reverse=True)

    return {
        "xg_home": round(xg_home, 2),
        "xg_away": round(xg_away, 2),
        "home": p_home / total,
        "draw": p_draw / total,
        "away": p_away / total,
        "over15": over15 / total,
        "under15": 1 - over15 / total,
        "over25": over25 / total,
        "under25": 1 - over25 / total,
        "over35": over35 / total,
        "under35": 1 - over35 / total,
        "btts": btts / total,
        "no_btts": 1 - btts / total,
        "scores": [
            {"score": f"{h}-{a}", "prob": p}
            for h, a, p in scores[:5]
        ]
    }


def get_last_matches(team_id, token):
    data = get_data(
        f"/teams/{team_id}/matches",
        token,
        {"status": "FINISHED", "limit": 20}
    )

    if not data or "error" in data:
        return []

    matches = data.get("matches", [])
    matches.sort(key=lambda x: x.get("utcDate", ""))
    return matches[-20:]


# =========================
# WEB API ROUTES
# =========================

@app.route("/")
def home():
    return render_template_string(HTML)


@app.route("/api/competitions", methods=["POST"])
def competitions():
    token = request.json.get("token", "").strip()

    if not token:
        return jsonify({"error": "دخل API KEY أولاً"}), 400

    data = get_data("/competitions", token)

    if not data or "error" in data:
        return jsonify(data or {"error": "ما قدرناش نجيبو البطولات"})

    comps = data.get("competitions", [])
    comps.sort(key=lambda c: (
        c.get("area", {}).get("name", ""),
        c.get("name", "")
    ))

    return jsonify([
        {
            "id": c.get("id"),
            "name": c.get("name"),
            "code": c.get("code"),
            "area": c.get("area", {}).get("name", "")
        }
        for c in comps
    ])


@app.route("/api/teams", methods=["POST"])
def teams():
    body = request.json
    token = body.get("token", "").strip()
    code = body.get("code", "").strip()

    data = get_data(f"/competitions/{code}/teams", token)

    if not data or "error" in data:
        return jsonify(data or {"error": "تعذر تحميل الفرق"})

    return jsonify([
        {"id": t["id"], "name": t["name"]}
        for t in data.get("teams", [])
    ])


@app.route("/api/analyze", methods=["POST"])
def analyze():
    body = request.json

    token = body.get("token", "").strip()
    home_id = int(body.get("home_id"))
    away_id = int(body.get("away_id"))

    if not token:
        return jsonify({"error": "API KEY مطلوب"})

    if home_id == away_id:
        return jsonify({"error": "اختار جوج فرق مختلفين"})

    hm = get_last_matches(home_id, token)
    am = get_last_matches(away_id, token)

    hs = team_statistics(hm, home_id)
    aws = team_statistics(am, away_id)

    if not hs or not aws:
        return jsonify({"error": "المباريات السابقة غير كافية للتحليل"})

    prediction = calculate_prediction(hs, aws)

    return jsonify({
        "home_stats": hs,
        "away_stats": aws,
        "prediction": prediction
    })


# =========================
# DASHBOARD
# =========================

HTML = r"""
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Football Predictor PRO</title>

<style>
*{box-sizing:border-box}
body{
    margin:0;
    background:#080d18;
    color:#f5f7fc;
    font-family:Arial,sans-serif;
    padding:18px 12px 35px;
}
.container{max-width:750px;margin:auto}
.header{
    display:flex;
    justify-content:space-between;
    align-items:center;
    margin:12px 4px 25px;
}
.logo{font-size:21px;font-weight:bold}
.version{
    background:#073b32;color:#38d6a4;
    padding:8px 13px;border-radius:20px;
    font-size:13px;font-weight:bold
}
.panel{
    background:#111a2b;
    border:1px solid #26334a;
    border-radius:25px;
    padding:20px;
    margin-bottom:17px;
}
h1{font-size:29px;margin:12px 0}
.subtitle{color:#a9b5c9;margin-bottom:22px}
.cards{
    display:grid;
    grid-template-columns:1fr 1fr;
    gap:12px;
}
.stat-card{
    background:#202d43;
    border-radius:19px;
    padding:18px;
}
.stat-card small{color:#aab6ca}
.stat-card strong{
    display:block;font-size:23px;margin-top:12px
}
.section-title{
    font-size:19px;font-weight:bold;
    margin:23px 0 14px
}
label{display:block;color:#b9c5d8;margin:13px 0 7px}
input,select{
    width:100%;padding:14px;
    background:#202d43;color:white;
    border:1px solid #34445e;
    border-radius:12px;font-size:15px;
}
button{
    width:100%;padding:15px;
    border:0;border-radius:13px;
    background:#27c99b;color:#061a19;
    font-weight:bold;font-size:16px;
    margin-top:17px;
}
button:disabled{opacity:.5}
.result{display:none}
.match{
    background:#202d43;
    border-radius:18px;padding:18px;
    margin-top:16px;
}
.teams{
    display:flex;justify-content:space-between;
    align-items:center;gap:8px;text-align:center;
    font-weight:bold;font-size:17px
}
.vs{color:#39d3a5;font-size:23px}
.probs{
    display:grid;grid-template-columns:repeat(3,1fr);
    text-align:center;margin-top:23px;
    border-top:1px solid #40516b;padding-top:18px;
    gap:5px
}
.probs strong{font-size:25px}
.probs small{display:block;color:#aab6ca;margin-top:8px}
.green{color:#39d3a5}
.bar{
    height:9px;background:#35445d;
    border-radius:20px;overflow:hidden;margin-top:8px
}
.fill{height:100%;background:#35d3a2;border-radius:20px}
.market{margin-top:15px}
.market-row{
    display:flex;justify-content:space-between;
    padding:12px 0;border-bottom:1px solid #34445e
}
.score-row{
    display:flex;justify-content:space-between;
    padding:11px;background:#26354d;
    border-radius:10px;margin-top:8px
}
.message{color:#ffd36a;margin-top:12px}
.error{color:#ff7777}
.footer{text-align:center;color:#74839b;font-size:12px;margin-top:25px}
@media(max-width:380px){
    h1{font-size:24px}
    .probs strong{font-size:21px}
}
</style>
</head>

<body>
<div class="container">

<div class="header">
    <div class="logo">⚽ FOOTBALL PREDICTOR</div>
    <div class="version">PRO V3.0</div>
</div>

<div class="panel">
    <h1>MATCH ANALYSIS</h1>
    <div class="subtitle">Advanced football prediction dashboard</div>

    <div class="cards">
        <div class="stat-card">
            <small>Analysis</small>
            <strong>Poisson</strong>
        </div>
        <div class="stat-card">
            <small>Mode</small>
            <strong>CLUBS</strong>
        </div>
    </div>

    <div class="section-title">إعداد التحليل</div>

    <label>Football-Data API KEY</label>
    <input id="token" type="password" placeholder="دخل API KEY ديالك">

    <button onclick="loadCompetitions()">تحميل البطولات</button>

    <label>البطولة</label>
    <select id="competition" onchange="loadTeams()">
        <option value="">اختار البطولة</option>
    </select>

    <label>الفريق المستضيف</label>
    <select id="home">
        <option value="">اختار الفريق</option>
    </select>

    <label>الفريق الضيف</label>
    <select id="away">
        <option value="">اختار الفريق</option>
    </select>

    <button id="analyze" onclick="analyzeMatch()">⚡ تحليل المباراة</button>
    <div id="message" class="message"></div>
</div>

<div class="panel result" id="result">

    <div class="version">PRO ANALYSIS</div>
    <div class="section-title">MATCH PREDICTION</div>

    <div class="match">
        <div class="teams">
            <span id="homeName">Home</span>
            <span class="vs">VS</span>
            <span id="awayName">Away</span>
        </div>

        <div class="probs">
            <div>
                <strong id="pHome">0%</strong>
                <small>1 - فوز المستضيف</small>
            </div>
            <div>
                <strong id="pDraw">0%</strong>
                <small>X - التعادل</small>
            </div>
            <div>
                <strong id="pAway">0%</strong>
                <small>2 - فوز الضيف</small>
            </div>
        </div>
    </div>

    <div class="section-title">EXPECTED GOALS - xG</div>
    <div class="cards">
        <div class="stat-card">
            <small id="xgHomeName">Home</small>
            <strong id="xgHome">0.00</strong>
        </div>
        <div class="stat-card">
            <small id="xgAwayName">Away</small>
            <strong id="xgAway">0.00</strong>
        </div>
    </div>

    <div class="section-title">OVER / UNDER</div>
    <div id="markets"></div>

    <div class="section-title">BTTS - الفريقان يسجلان</div>
    <div id="btts"></div>

    <div class="section-title">TOP 5 CORRECT SCORES</div>
    <div id="scores"></div>

    <div class="section-title">TEAM STATISTICS</div>
    <div id="stats"></div>

    <p class="message">
        تنبيه: هاد النسب تقديرية وماشي ضمان للنتيجة.
    </p>
</div>

<div class="footer">
    FOOTBALL PREDICTOR PRO V3.0 • CLUB ANALYSIS
</div>
</div>

<script>
const $ = id => document.getElementById(id);

function token(){
    return $("token").value.trim();
}

function showMessage(msg,error=false){
    $("message").textContent=msg;
    $("message").className=error?"message error":"message";
}

async function post(url,data){
    const response=await fetch(url,{
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify(data)
    });
    return await response.json();
}

async function loadCompetitions(){
    if(!token()) return showMessage("دخل API KEY أولاً",true);

    showMessage("جاري تحميل البطولات...");
    try{
        const data=await post("/api/competitions",{token:token()});
        if(data.error) return showMessage(data.error,true);

        $("competition").innerHTML='<option value="">اختار البطولة</option>';

        data.forEach(c=>{
            const option=document.createElement("option");
            option.value=c.code;
            option.textContent=c.name+" | "+c.area;
            $("competition").appendChild(option);
        });

        showMessage("تم تحميل "+data.length+" بطولة متاحة لحسابك");
    }catch(e){
        showMessage("وقع مشكل في الاتصال بالسيرفر",true);
    }
}

async function loadTeams(){
    const code=$("competition").value;
    if(!code)return;

    showMessage("جاري تحميل الفرق...");
    const data=await post("/api/teams",{
        token:token(),code:code
    });

    if(data.error)return showMessage(data.error,true);

    ["home","away"].forEach(id=>{
        $(id).innerHTML='<option value="">اختار الفريق</option>';
        data.forEach(t=>{
            const op=document.createElement("option");
            op.value=t.id;
            op.textContent=t.name;
            $(id).appendChild(op);
        });
    });

    showMessage("تم تحميل الفرق");
}

function percent(v){
    return (v*100).toFixed(1)+"%";
}

function marketRow(label,value){
    return `
    <div class="market">
      <div class="market-row">
        <span>${label}</span><strong>${percent(value)}</strong>
      </div>
      <div class="bar"><div class="fill" style="width:${value*100}%"></div></div>
    </div>`;
}

async function analyzeMatch(){
    const h=$("home").value;
    const a=$("away").value;

    if(!token())return showMessage("دخل API KEY",true);
    if(!h||!a)return showMessage("اختار الفريقين",true);
    if(h===a)return showMessage("اختار فريقين مختلفين",true);

    $("analyze").disabled=true;
    $("result").style.display="none";
    showMessage("جاري تحليل آخر المباريات...");

    try{
        const data=await post("/api/analyze",{
            token:token(),home_id:h,away_id:a
        });

        if(data.error){
            showMessage(data.error,true);
            return;
        }

        const p=data.prediction;
        const hs=data.home_stats;
        const as=data.away_stats;

        $("homeName").textContent=$("home").selectedOptions[0].textContent;
        $("awayName").textContent=$("away").selectedOptions[0].textContent;

        $("pHome").textContent=percent(p.home);
        $("pDraw").textContent=percent(p.draw);
        $("pAway").textContent=percent(p.away);

        const best=Math.max(p.home,p.draw,p.away);
        const bestId=best===p.home?"pHome":best===p.draw?"pDraw":"pAway";
        $(bestId).classList.add("green");

        $("xgHomeName").textContent=$("homeName").textContent;
        $("xgAwayName").textContent=$("awayName").textContent;
        $("xgHome").textContent=p.xg_home;
        $("xgAway").textContent=p.xg_away;

        $("markets").innerHTML=
            marketRow("Over 1.5",p.over15)+
            marketRow("Under 1.5",p.under15)+
            marketRow("Over 2.5",p.over25)+
            marketRow("Under 2.5",p.under25)+
            marketRow("Over 3.5",p.over35)+
            marketRow("Under 3.5",p.under35);

        $("btts").innerHTML=
            marketRow("نعم - الفريقان يسجلان",p.btts)+
            marketRow("لا - ليس كلاهما",p.no_btts);

        $("scores").innerHTML=p.scores.map(s=>`
            <div class="score-row">
                <strong>${s.score}</strong>
                <span class="green">${percent(s.prob)}</span>
            </div>`).join("");

        function teamStats(name,s){
            return `<div class="match">
                <strong>${name}</strong>
                <div class="market-row"><span>المباريات</span><b>${s.played}</b></div>
                <div class="market-row"><span>الفوز</span><b>${s.wins}</b></div>
                <div class="market-row"><span>التعادل</span><b>${s.draws}</b></div>
                <div class="market-row"><span>الخسارة</span><b>${s.losses}</b></div>
                <div class="market-row"><span>معدل التسجيل</span><b>${s.gf.toFixed(2)}</b></div>
                <div class="market-row"><span>معدل الاستقبال</span><b>${s.ga.toFixed(2)}</b></div>
            </div>`;
        }

        $("stats").innerHTML=
            teamStats($("homeName").textContent,hs)+
            teamStats($("awayName").textContent,as);

        $("result").style.display="block";
        showMessage("اكتمل التحليل بنجاح");
        $("result").scrollIntoView({behavior:"smooth"});

    }catch(e){
        showMessage("وقع خطأ أثناء التحليل",true);
    }finally{
        $("analyze").disabled=false;
    }
}
</script>
</body>
</html>
"""


if __name__ == "__main__":
    print("FOOTBALL PREDICTOR PRO V3.0")
    print("فتح المتصفح على: http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)
