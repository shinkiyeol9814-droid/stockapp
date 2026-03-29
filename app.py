import streamlit as st
import FinanceDataReader as fdr
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import io
import re
import requests
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# --- 페이지 기본 설정 ---
st.set_page_config(page_title="StkPro 가치평가", page_icon="📈", layout="wide")

# 💡 강제 라이트모드 절대 금지 (테마 적응형) 및 수직 정렬 유지 CSS
st.markdown("""
    <style>
        /* 타이틀 여백 정상화 */
        .block-container { padding-top: 2.5rem !important; padding-bottom: 1rem !important; padding-left: 0.8rem !important; padding-right: 0.8rem !important; }
        .main-title { font-size: 1.4rem !important; font-weight: bold; margin-top: 1rem; margin-bottom: 1rem; }
        .sub-header { font-size: 1.1rem !important; font-weight: bold; margin-top: 10px; margin-bottom: 10px; }
        
        /* 카드형 UI */
        .info-box { background-color: rgba(128, 128, 128, 0.05) !important; padding: 12px 15px; border-radius: 10px; margin-bottom: 15px; border: 1px solid rgba(128, 128, 128, 0.2) !important; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
        
        /* 수직 중앙 정렬 유지 */
        .info-row { 
            display: flex; 
            flex-direction: row; 
            justify-content: flex-start; 
            align-items: center !important; 
            border-bottom: 1px solid rgba(128, 128, 128, 0.1) !important; 
            padding-bottom: 8px; 
            margin-bottom: 8px; 
            white-space: nowrap; 
            overflow-x: auto; 
            height: 24px; 
        }
        .info-row:last-child { border-bottom: none; padding-bottom: 0; margin-bottom: 0; }
        
        /* 다크모드 대응 (opacity 활용) */
        .col-title { width: 90px; font-weight: bold; font-size: 13px; text-align: left; flex-shrink: 0; line-height: 1.5; margin-top: 2px; opacity: 0.85; }
        .col-divider { margin: 0 8px; flex-shrink: 0; line-height: 1.5; opacity: 0.3; }
        .col-price { width: 80px; font-weight: bold; font-size: 15px; text-align: right; flex-shrink: 0; line-height: 1.5; }
        .col-marcap { width: 75px; font-size: 12px; text-align: right; flex-shrink: 0; line-height: 1.5; opacity: 0.6; }
        .col-rate { width: 100px; font-weight: bold; font-size: 14px; text-align: right; flex-shrink: 0; line-height: 1.5; }
        
        /* 등락률 색상 고정 */
        .rate-up { color: #ff4b4b !important; }
        .rate-down { color: #0068c9 !important; }
        .rate-none { opacity: 0.5; }

        /* 검색폼 한 줄 정렬 */
        .search-container { display: flex; align-items: center; margin-bottom: 10px; width: 100%; }
        .search-label { font-size: 14px; font-weight: bold; margin-right: 10px; white-space: nowrap; }
        .search-input-wrap { flex-grow: 1; margin-right: 8px; }
        
        /* 폼 컨트롤 여백 제거 */
        .stTextInput, .stSelectbox, .stNumberInput { margin-bottom: -15px !important; }
        .stTextInput > div > div > input, .stSelectbox > div > div > div, .stNumberInput > div > div > input { 
            height: 36px !important; min-height: 36px !important; font-size: 13px !important; padding: 0 8px !important; 
        }
        
        /* 갱신 버튼 */
        div.stButton > button { 
            height: 36px !important; min-height:
