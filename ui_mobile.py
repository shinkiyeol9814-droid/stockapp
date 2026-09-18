"""
ui_mobile.py — 모바일에서 거슬리는 Streamlit 기본 동작을 눌러주는 보정.

지금은 하나뿐이다: selectbox의 키패드 억제.

Streamlit selectbox는 안쪽이 `<input type="text">`라 탭하면 모바일 키보드가
올라온다. 옵션이 11개짜리 '테마'처럼 타이핑할 이유가 없는 셀렉터에서는
키패드가 화면 절반을 덮고 목록을 가려 순전히 방해다.

Streamlit에는 검색을 끄는 옵션이 없어서(1.55 기준) input에 readonly를 건다.
BaseWeb 셀렉트는 input 클릭으로 드롭다운을 여는데, readonly여도 클릭 이벤트는
그대로 살아 있어서 목록은 정상으로 열린다(실측 확인).

⚠️ 종목명 자동완성(가치평가)이나 업종 선택(섹터별)처럼 **타이핑이 핵심인**
셀렉터에는 걸면 안 된다. 그래서 전역이 아니라 키를 지정해 거는 방식이다.
"""
import json

import streamlit.components.v1 as components


def disable_keyboard(*key_prefixes: str) -> None:
    """
    지정한 키(접두사)를 가진 selectbox에서 모바일 키패드가 뜨지 않게 한다.

    st.selectbox(..., key="trade_theme") 처럼 key를 준 위젯은 컨테이너에
    `st-key-trade_theme` 클래스가 붙는다. 그 클래스를 접두사로 찾는다 —
    key가 f"trade_country_{theme}" 처럼 동적이면 한글이 '-'로 치환돼
    정확한 이름을 알 수 없으므로 접두사 매칭이 필요하다.
    """
    if not key_prefixes:
        return
    selector = ", ".join(f'[class*="st-key-{k}"] input' for k in key_prefixes)
    components.html(
        """
        <script>
        (function () {
          const win = window.parent || window;
          const doc = win.document;
          const SEL = %s;

          // 여러 탭에서 각자 호출하므로 선택자를 모아 관찰자는 하나만 둔다.
          win.__noKbSel = win.__noKbSel || new Set();
          win.__noKbSel.add(SEL);

          function patch() {
            win.__noKbSel.forEach(function (s) {
              doc.querySelectorAll(s).forEach(function (i) {
                if (i.dataset.noKb) return;      // 이미 처리한 input은 건너뛴다
                i.dataset.noKb = "1";
                i.readOnly = true;               // 키패드가 뜨지 않는다
                i.setAttribute("inputmode", "none");
                i.style.caretColor = "transparent";
              });
            });
          }
          patch();

          // Streamlit은 조작마다 위젯을 다시 그린다. 새로 생긴 input에도 걸어야
          // 하지만, 리렌더 중 변경이 매우 잦아 프레임당 한 번으로 묶는다.
          if (!win.__noKbObs) {
            win.__noKbObs = new MutationObserver(function () {
              if (win.__noKbPending) return;
              win.__noKbPending = true;
              win.requestAnimationFrame(function () {
                win.__noKbPending = false;
                patch();
              });
            });
            win.__noKbObs.observe(doc.body, { childList: true, subtree: true });
          }
        })();
        </script>
        """ % json.dumps(selector),
        height=0,
    )
