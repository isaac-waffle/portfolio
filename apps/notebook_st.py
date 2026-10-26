"""Streamlit 앱(트레이딩 데스크·어닝 대시보드)용 공책 테마.

색은 .streamlit/config.toml([theme.light]·[theme.dark]), 줄 노트 종이·펜 테두리·손글씨 제목은 여기 CSS.
허브·가상 트레이더(notebook.css)와 같은 규칙: 손글씨는 큰 제목(st.title·st.header)에만,
나머지(숫자·표·라벨·본문)는 1초에 읽히도록 시스템 글꼴 그대로. 빨간 여백 선 없음.
"""
import streamlit as st

CSS = """<style>
@import url('https://fonts.googleapis.com/css2?family=Gaegu:wght@400;700&display=swap');
:root{--ink:#26388a;--rule:rgba(90,135,210,.22);--nb-line:rgba(35,52,126,.18);--nb-card:#fffdf6;
  --nb-shadow:2px 3px 0 rgba(35,52,126,.10);--hand:"Gaegu","Nanum Pen Script",cursive;
  --sketch:255px 14px 225px 16px/16px 225px 14px 255px;--sketch2:16px 225px 14px 255px/255px 14px 225px 16px}
@media (prefers-color-scheme:dark){:root{--ink:#a9b8ff;--rule:rgba(140,165,230,.10);--nb-line:rgba(170,185,255,.18);
  --nb-card:#202229;--nb-shadow:2px 3px 0 rgba(0,0,0,.35)}}
.stApp{background-image:repeating-linear-gradient(180deg,transparent 0,transparent 31px,var(--rule) 31px,var(--rule) 32px)}
[data-testid="stHeader"]{background:transparent}
h1{font-family:var(--hand)!important;font-weight:700!important;color:var(--ink)!important;letter-spacing:0!important;
  font-size:3.1rem!important;text-decoration:underline wavy color-mix(in srgb,var(--ink) 40%,transparent);
  text-decoration-thickness:2px;text-underline-offset:10px}
h2{font-family:var(--hand)!important;font-weight:700!important;color:var(--ink)!important;letter-spacing:0!important;font-size:2.1rem!important}
h3{color:var(--ink)!important}
/* 카드류: 펜으로 그린 테두리 */
.tile,.card,.vcard,[data-testid="stMetric"],[data-testid="stVerticalBlockBorderWrapper"]:has(> div > [data-testid="stVerticalBlock"]){
  background:var(--nb-card)!important;border:2px solid var(--ink)!important;border-radius:var(--sketch)!important;box-shadow:var(--nb-shadow)}
.tile:nth-child(even),.vcard:nth-child(even){border-radius:var(--sketch2)!important}
[data-testid="stMetric"]{padding:10px 14px}
.vcard.active{border-width:3.5px!important}
[data-testid="stExpander"] details{border:2px solid var(--ink)!important;border-radius:var(--sketch)!important;background:var(--nb-card)}
.stButton button,.stDownloadButton button,[data-testid="stLinkButton"] a{border:1.5px solid var(--ink)!important;border-radius:var(--sketch)!important}
hr{border-top:1.5px dashed var(--nb-line)!important}
.pill{background:transparent!important;border:1.5px solid var(--nb-line);border-radius:var(--sketch)!important}
.kv{border-top:1.5px dashed var(--nb-line)!important}
</style>"""


def apply():
    st.markdown(CSS, unsafe_allow_html=True)
