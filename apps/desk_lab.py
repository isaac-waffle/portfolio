"""퀀트 데스크(알파) 섞는 방법 비교 — 지금(20%×5)과 대안들. (.venv/bin/python apps/desk_lab.py)

수익률 단위로 섞음(전략별 하루 수익률 × 몫, 총 비중이 95%를 넘으면 그만큼 줄임). 2016~, 종목 전략은 생존 편향 있음.

결과 (2026-11-17) — 규칙은 안 바꿈
  지금 데스크: 장기 +18%/y 샤프 1.06 낙폭 −19% · 학습 1.41 · 확인 2.21 · 시험 1.41. 평균 총 비중 61% (39%는 현금 대기).
  전체 1.25배(평균 비중 75%): 샤프는 그대로(1.05·1.40·2.22·1.41), 수익과 낙폭이 같이 커짐(장기 +25%/y, −23%) — 실력이 아니라 위험을 더 지는 선택이라 사용자 결정.
  IBS(QQQ 종가 매매) 추가: 장기 샤프 1.06→1.20이지만 2020년(+84%) 덕이고 학습·확인·시험은 1.39·2.20·1.32로 오히려 조금 낮음. 종가 주문도 새로 만들어야 해서 안 넣음.
  변동성 역가중: 샤프 비슷(1.11·1.36·2.27·1.29), 수익은 줄어듦. 안 바꿈.
"""
import numpy as np, pandas as pd, strategy_lab as L, paper_desk as P
E,S=L.load()
strat=L.etf_strategies(E)|L.stock_strategies(S)
end=S["Close"].index[-1]
R,G={},{}
for name,(W,mode) in strat.items():
    F=E if set(W.columns)<=set(E["Close"].columns) else S
    R[name]=L.bt(W,F,mode); G[name]=W.fillna(0).sum(1).shift(1)
R=pd.DataFrame(R).loc["2016-01-01":end]; G=pd.DataFrame(G).reindex(R.index).fillna(0)
PER={"장기":("2016-01-01","2023-12-31"),"학습":("2024-01-01","2025-06-30"),"확인":("2025-07-01","2025-12-31"),"시험":("2026-01-01","2026-12-31")}
def line(r):
    out=[]
    for k,(a,b) in PER.items():
        s=L.stat(r,a,b); out.append(f"{k} {s['ret']*100/ (len(r[a:b])/252):+6.1f}%/y S{s['sharpe']:+.2f} DD{s['dd']*100:4.0f}%")
    return " | ".join(out)
def blend(sh):
    g=sum(G[n]*w for n,w in sh.items()); sc=(0.95/g).clip(upper=1).fillna(1)
    return sum(R[n]*w for n,w in sh.items())*sc, (g*sc)
base={n:0.2 for n in P.SLEEVES}
def show(tag,sh):
    r,g=blend(sh); yrs={y:f"{((1+r[str(y)]).prod()-1)*100:+.0f}%" for y in (2018,2020,2022)}
    print(f"{tag:34s} {line(r)} | 평균 비중 {g.mean():.0%} | {yrs}")
    return r
r0=show("현재 (20%×5)",base)
for m in (1.25,1.5,1.75): show(f"전체 {m}배 (상한 95%)",{n:0.2*m for n in base})
show("+ IBS 20%",base|{"ibs":0.2}); show("+ IBS 30%",base|{"ibs":0.3})
show("+ RSI2 20%",base|{"rsi2":0.2}); show("+ IBS 20% + RSI2 20%",base|{"ibs":0.2,"rsi2":0.2})
show("1.25배 + IBS 25%",{n:0.25 for n in base}|{"ibs":0.25})
show("1.5배 + IBS 30%",{n:0.3 for n in base}|{"ibs":0.3})
show("ep 빼고 IBS (20%×5)",{n:0.2 for n in base if n!="ep"}|{"ibs":0.2})
vol=R[list(base)].rolling(60).std().shift(1)
iv=(1/vol).div((1/vol).sum(1),axis=0)
r=(R[list(base)]*iv).sum(1); print(f"{'변동성 역가중':34s} {line(r)}")
show("SPY 보유",{"SPY 보유":0.95})
# IBS 단독 연도별
print("IBS 연도별:", {y:f"{((1+R['ibs'][str(y)]).prod()-1)*100:+.0f}%" for y in range(2016,2027)}, "보유일 비율", round(float((R['ibs']!=0).mean()),2))
