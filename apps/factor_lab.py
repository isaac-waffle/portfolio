"""거래량·계절성이 주가를 맞히는지 — 트레이더가 '고려'하게 하기 전에 일봉 이력으로 확인. (.venv/bin/python apps/factor_lab.py)

종목 신호는 매주 상위 10종목 포트폴리오를 '전 종목 균등 보유'와 비교(초과 수익·t값), 시장 계절성은 SPY 하루 수익률을 조건별로 비교.
구간: 장기(~2023) / 학습(24.01~25.06) / 확인(25.07~12) / 시험(26.01~). 종목은 나스닥100+관심종목(지금 구성 — 생존 편향).

결과 (2026-11-17) — 매매 신호로 쓸 만한 것 없음, 화면·설명의 참고 정보로만 씀
  거래량: 급증(5일/50일), 급증+상승, 급증+하락, 상승일 거래량 비중, 거래량 마름 — 장기 구간에서 전부 균등 보유보다 못하고(초과 −3~−12%p), 최근 구간은 부호가 제각각.
  종목 계절성(과거 같은 달 평균 상위 10): 장기 −2%p · 학습 +16 · 확인 +8 · 시험 −10 — t값 전부 1 미만.
  시장 계절성: 요일·월·월말월초·11~4월·옵션 만기 주 모두 구간마다 부호가 바뀜 (예: 월요일 장기 +2bp, 최근 +31bp / 월말월초 학습 −5bp, 시험 +27bp).
"""
import numpy as np, pandas as pd, strategy_lab as L
E,S=L.load()
O,H,Lo,C,V=(S[k] for k in ("Open","High","Low","Close","Volume"))
days=C.index; we=L.week_end(days); me=L.month_end(days)
liquid=((C*V).rolling(20).mean()>=2e7)&(C>=5)
PER={"장기":("2016-01-01","2023-12-31"),"학습":("2024-01-01","2025-06-30"),"확인":("2025-07-01","2025-12-31"),"시험":("2026-01-01","2026-12-31")}
eq=liquid.astype(float); base=L.bt(L.hold(eq.div(eq.sum(1).clip(lower=1),axis=0),we),S,"open")
def show(name,W,ref=base):
    r=L.bt(W,S,"open"); x=r-ref; out=[]
    for k,(a,b) in PER.items():
        s=L.stat(r,a,b); xx=x[a:b]
        out.append(f"{k} {s['ret']/(len(xx)/252)*100:+6.1f}%/y S{s['sharpe']:+.2f} 초과 {xx.mean()*252*100:+5.1f}%p t{xx.mean()/xx.std()*np.sqrt(len(xx)):+.1f}")
    print(f"{name:30s} "+" | ".join(out))
ret=C.pct_change()
print("── 거래량 (매주 상위 10)")
vr5=V.rolling(5).mean()/V.rolling(50).mean()
show("거래량 급증 (5일/50일)",L.hold(L.topn(vr5,10,liquid),we))
show("거래량 급증 + 5일 상승",L.hold(L.topn(vr5,10,liquid&(C>C.shift(5))),we))
show("거래량 급증 + 5일 하락",L.hold(L.topn(vr5,10,liquid&(C<C.shift(5))),we))
upv=(V.where(ret>0,0)).rolling(20).sum()/V.rolling(20).sum()
show("상승일 거래량 비중 20일",L.hold(L.topn(upv,10,liquid),we))
show("거래량 마름 (5일/50일 하위)",L.hold(L.topn(-vr5,10,liquid&(C>C.rolling(50).mean())),we))
mom=C.shift(21)/C.shift(252)-1
show("12개월 모멘텀 상위10 (비교)",L.hold(L.topn(mom,10,liquid),me))
show("모멘텀 상위30 중 상승거래량 상위10",L.hold(L.topn(upv,10,liquid&(mom.rank(axis=1,ascending=False)<=30)),we))
print("── 계절성 (종목별: 과거 같은 달 평균 수익률 상위 10, 매월)")
mret=C.resample("ME").last().pct_change()
same=pd.DataFrame(index=mret.index,columns=mret.columns,dtype=float)
for i,d in enumerate(mret.index):          # 다음 달과 같은 달의 과거 평균 (최소 3년)
    nm=(d.month%12)+1; past=mret[(mret.index<=d)&(mret.index.month==nm)]
    same.iloc[i]=past.mean().where(past.count()>=3)
sd=same.reindex(days,method="ffill").shift(1)   # 월말 값이 다음 달 내내 유효; 월말일에 리밸런스
sdm=same.copy(); sdm.index=[days[days<=d][-1] for d in same.index]; sig=sdm.reindex(days)
show("같은 달 계절성 상위10",L.hold(L.topn(sig.ffill(),10,liquid),me))
print("── 계절성 (시장: SPY)")
spy=E["Close"]["SPY"]; so=E["Open"]["SPY"]; r=spy.pct_change()
def per(x,name):
    out=[]
    for k,(a,b) in {"장기":("2007-01-01","2023-12-31"),**{k:v for k,v in PER.items() if k!="장기"}}.items():
        v=x[a:b].dropna(); out.append(f"{k} {v.mean()*1e4:+5.1f}bp/일 t{v.mean()/v.std()*np.sqrt(len(v)):+.1f} ({len(v)})")
    print(f"{name:30s} "+" | ".join(out))
per(r,"모든 날")
for i,n in enumerate(["월","화","수","목","금"]): per(r[r.index.dayofweek==i],f"{n}요일")
for m in range(1,13): per(r[r.index.month==m],f"{m}월")
dom=pd.Series(1,index=spy.index).groupby(spy.index.to_period("M")).cumsum()
rem=pd.Series(1,index=spy.index)[::-1].groupby(spy.index.to_period("M")[::-1]).cumsum()[::-1]
per(r[(dom<=3)|(rem<=1)],"월말 마지막날~월초 3일"); per(r[(dom>3)&(rem>1)],"그 외")
per(r[(r.index.month>=11)|(r.index.month<=4)],"11~4월"); per(r[(r.index.month>=5)&(r.index.month<=10)],"5~10월")
opex=(spy.index.dayofweek==4)&(spy.index.day>=15)&(spy.index.day<=21)
wk=spy.index.to_period("W"); opw=pd.Series(opex,index=spy.index).groupby(wk).transform("max")
per(r[opw.values],"옵션 만기 주"); per(r[~opw.values],"만기 주 아님")
