"""
generate_telegram_session.py — 새 텔레그램 세션 문자열 발급용 로그인 스크립트.

⚠️ 반드시 본인 PC(로컬)에서 직접 실행하세요.
   GitHub Actions나 Streamlit Cloud 같은 서버에서 실행하면 안 됩니다 —
   전화번호 인증 코드를 대화형으로 직접 입력해야 하기 때문입니다.

사용법:
    pip install telethon
    python generate_telegram_session.py

    API ID / API Hash는 https://my.telegram.org 에서 발급받은 값을 입력합니다
    (Streamlit Secrets의 TELEGRAM_API_ID / TELEGRAM_API_HASH와 동일한 값).
    전화번호 → 인증코드(→ 2단계 인증 비밀번호, 설정된 경우) 순으로 입력하면
    새 세션 문자열이 출력됩니다.

세션을 용도별로 분리하려면 이 스크립트를 여러 번 실행해서 각각 다른
세션 문자열을 받으세요 (같은 계정으로 로그인해도 매번 새 세션이 발급됩니다):
  - 앱용(Streamlit Cloud, 상시 구동)  → TELEGRAM_SESSION
  - 배치용(GitHub Actions, 3개 워크플로우 공용) → TELEGRAM_SESSION_BATCH
같은 세션을 여러 프로세스가 동시에(다른 IP에서) 쓰면 텔레그램이 세션을
통째로 무효화(AuthKeyDuplicatedError)하므로, 반드시 별도 세션으로 나눠서
각 시크릿에 넣어주세요.
"""
import os

from telethon.sync import TelegramClient
from telethon.sessions import StringSession


def main():
    api_id = os.environ.get("TELEGRAM_API_ID") or input("API ID: ").strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH") or input("API Hash: ").strip()

    with TelegramClient(StringSession(), int(api_id), api_hash) as client:
        session_str = client.session.save()

    print("\n" + "=" * 70)
    print("새로 발급된 세션 문자열입니다. 아래 값을 복사해 시크릿에 붙여넣으세요.")
    print("(앱용/배치용을 나누는 경우, 이 스크립트를 다시 실행해서 두 번째")
    print(" 세션도 따로 받으세요.)")
    print("=" * 70)
    print(session_str)
    print("=" * 70)
    print("\n⚠️  이 문자열은 비밀번호와 동일하게 취급하세요.")
    print("    절대 커밋하거나 채팅/이슈 등에 붙여넣지 마세요.")


if __name__ == "__main__":
    main()
