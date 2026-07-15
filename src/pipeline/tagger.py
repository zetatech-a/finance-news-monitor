from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from src.pipeline.normalize import Article
from src.pipeline.text_matcher import contains_term, has_any_term, normalize_text


@dataclass
class TaggedArticle:
    article: Article
    sectors: list[str]
    topics: list[str]
    matched_keywords: list[str]


# NOTE: 일부 짧은 키워드는 다른 단어의 접두/부분문자열로 자주 등장해 오탐을 유발함.
# 예) '리스' -> '리스크' (해외/시장 기사에 빈번)
RISKY_SHORT_KEYWORDS = {"대부", "여전"}

TITLE_STRONG_SCORE = 6
TITLE_WEAK_SCORE = 3
TITLE_GENERIC_SCORE = 1
BODY_STRONG_SCORE = 3
BODY_WEAK_SCORE = 1
BODY_GENERIC_SCORE = 1
TITLE_NEGATIVE_PENALTY = 6
BODY_NEGATIVE_PENALTY = 3
PRIMARY_SECTOR_THRESHOLD = 4

TOPIC_TITLE_STRONG_SCORE = 4
TOPIC_TITLE_WEAK_SCORE = 2
TOPIC_BODY_STRONG_SCORE = 2
TOPIC_BODY_WEAK_SCORE = 1
TOPIC_QUERY_AUX_SCORE = 0.15
TOPIC_TITLE_NEGATIVE_PENALTY = 4
TOPIC_BODY_NEGATIVE_PENALTY = 2
TOPIC_TITLE_CONTEXT_SCORE = 1.2
TOPIC_BODY_CONTEXT_SCORE = 0.6
TOPIC_SECTOR_AUX_SCORE = 0.8
DEFAULT_TOPIC_THRESHOLD = 2.8

GENERIC_SECTOR_TOKENS = {
    "은행",
    "인터넷은행",
    "은행권",
    "펀드",
    "거래소",
    "대출",
}

TEXT_ALIASES: tuple[tuple[str, str], ...] = (
    ("investment bank", "투자은행"),
    ("investment banking", "투자은행"),
    ("인터넷 전문은행", "인터넷은행"),
    ("가상화폐", "암호화폐"),
    ("코인거래소", "가상자산 거래소"),
    ("美국채", "미국채"),
    ("美 증시", "미국 증시"),
    ("美 연준", "미국 연준"),
    ("美", "미국"),
    ("인뱅", "인터넷은행"),
    ("마통", "마이너스통장"),
    ("빚투", "대출투자"),
    ("보이싱피싱", "보이스피싱"),
)

SECTOR_RULE_OVERRIDES: dict[str, dict[str, list[str]]] = {
    "대부": {
        "strong": [
            "대부업", "대부업권", "대부업계", "대부업체", "대부업자", "대부중개", "대부중개업", "대부업법",
            "등록대부업체", "미등록대부", "불법대부", "불법사금융", "불법사채", "사채업자", "고리대금", "고리대금업",
            "상품권 사채", "내구제대출", "내구제 대출", "불법추심", "채권추심", "대부채권", "새도약기금", "장기연체채권",
            "추심중단", "불법 대출광고",
        ],
        "weak": ["채권추심", "최고금리", "대부"],
        "negative": [
            "공유재산 대부", "공유재산 대부계약", "지자체 공유재산 대부", "국유재산 대부료", "대부계약", "대부료",
            "토지 대부", "농지 대부", "공공시설 대부", "도서 대부", "무상대부", "대부도",
        ],
        "demote_to_weak": ["최고금리", "채권추심"],
    },
    "은행": {
        "strong": [
            "시중은행", "예대금리차", "예금은행", "은행채", "은행권", "인터넷은행", "국책은행",
            "kb국민은행", "국민은행", "신한은행", "우리은행", "하나은행", "ibk기업은행", "기업은행",
            "nh농협은행", "농협은행", "수협은행", "sh수협은행", "sc제일은행", "씨티은행", "산업은행",
            "수출입은행", "부산은행", "경남은행", "광주은행", "전북은행", "대구은행", "카카오뱅크",
            "케이뱅크", "토스뱅크", "은행권 대출", "은행권 수익", "은행권 예대금리차",
        ],
        "weak": ["은행", "인터넷은행", "시중은행", "국책은행"],
        "negative": ["투자은행", "저축은행", "상호저축은행", "ipo", "회사채", "ecm", "dcm"],
    },
    "저축은행": {
        "strong": ["저축은행", "상호저축은행", "sbi저축은행", "ok저축은행", "상상인저축은행"],
        "weak": ["상상인"],
        "negative": ["은행권", "시중은행", "투자은행"],
    },
    "상호금융": {
        "strong": [
            "상호금융", "상호금융권", "신협", "신협중앙회", "새마을금고", "농협 상호금융",
            "농협중앙회 상호금융", "지역농협", "단위농협", "수협 상호금융", "산림조합 금융", "산림조합",
        ],
        "weak": ["농협", "수협", "조합"],
        "negative": [
            "여신협회", "여신금융협회", "여신전문금융협회", "여신전문금융업", "농협은행", "nh농협은행",
            "수협은행", "은행권", "카드사", "캐피탈사", "저축은행", "상호저축은행",
        ],
        "demote_to_weak": ["농협", "수협", "조합"],
    },
    "여전": {
        "strong": [
            "여신금융협회", "여신협회", "여신전문금융협회", "여신전문금융업", "여신전문금융회사",
            "여전사", "여전업계", "카드사", "카드업계", "카드론", "현금서비스", "신용판매",
            "가맹점 수수료", "카드수수료", "할부금융", "리스금융", "캐피탈사", "캐피탈업계", "자동차금융", "렌탈금융",
        ],
        "weak": ["여전", "여전채", "카드", "캐피탈", "할부", "리스"],
        "negative": ["신협", "상호금융", "새마을금고", "농협 상호금융", "수협 상호금융", "보험사", "은행권"],
        "demote_to_weak": ["여전", "여전채", "캐피탈", "리스"],
    },
    "보험": {
        "strong": [
            "보험사", "보험업계", "생명보험", "손해보험", "생보사", "손보사", "실손보험", "자동차보험",
            "보험금 지급", "보험계약", "보험영업", "보험설계사", "보험대리점", "ga", "킥스", "k-ics", "rbc",
            "지급여력비율", "코리안리", "재보험사", "재보험 시장",
        ],
        "weak": ["보험", "보험료", "재보험"],
        "negative": [
            "건강보험", "고용보험", "산재보험", "국민건강보험", "운송·보험료", "운송 보험료", "물류 보험료",
            "여행자보험", "항공사", "일반 기업", "비용성 보험료",
        ],
        "demote_to_weak": ["보험", "보험료", "재보험"],
    },
    "증권(브로커리지/리테일)": {
        "strong": [
            "증권사 리테일", "브로커리지", "위탁매매", "주식거래 수수료", "개인투자자 예탁금", "신용융자",
            "미수거래", "증권계좌", "공매도 제재", "증권사",
        ],
        "weak": ["리테일", "주식거래"],
        "negative": ["토큰증권", "한국거래소", "유가증권시장"],
    },
    "자산운용·연기금": {
        "strong": ["자산운용사", "자산운용", "운용사", "국민연금", "연기금", "etf 운용", "펀드 설정", "사모펀드", "공모펀드"],
        "weak": ["etf", "펀드"],
        "negative": ["펀드 사기", "크라우드펀드"],
        "demote_to_weak": ["ETF", "펀드"],
    },
    "IB·자본시장": {
        "strong": [
            "ipo 주관", "상장 주관", "공모주 주관", "ecm", "dcm", "회사채 발행", "cp시장", "abcp", "주관사", "인수단",
            "채권발행시장", "투자은행", "공모주", "미매각", "스프레드",
        ],
        "weak": ["ipo", "상장", "회사채", "cp", "m&a"],
        "negative": ["시중은행", "예대금리차", "인터넷은행", "은행권 대출"],
        "demote_to_weak": ["IPO", "회사채", "CP", "M&A"],
    },
    "디지털자산": {
        "strong": [
            "가상자산", "암호화폐", "코인거래소", "업비트", "빗썸", "두나무", "토큰증권", "sto", "디지털자산거래소", "디지털자산",
        ],
        "weak": ["거래소", "코인"],
        "negative": ["한국거래소", "유가증권시장", "코스닥", "코스피", "거래소 단독"],
        "demote_to_weak": ["거래소"],
    },
    "핀테크·플랫폼": {
        "strong": ["핀테크", "마이데이터", "간편결제", "금융플랫폼", "대출비교", "대출모집", "pg사"],
        "weak": ["pg", "대출"],
        "negative": ["프로야구", "게임 플랫폼"],
        "demote_to_weak": ["대출", "PG"],
    },
    "거시·시장": {
        "strong": [
            "외환시장", "금융시장", "채권시장", "국채금리", "국고채", "원달러 환율", "원/달러", "기준금리",
            "fomc", "연준", "한국은행", "한은", "코스피", "코스닥", "증시", "뉴욕증시", "시장금리",
        ],
        "weak": ["환율", "금리", "유가", "달러", "국채", "인플레이션", "cpi", "pce", "경기침체"],
        "negative": ["영업이익", "항공사", "운송", "원가 부담"],
        "demote_to_weak": ["환율", "달러", "국채", "인플레이션", "CPI", "PCE", "경기침체", "유가"],
    },
    "감독·제재": {
        "strong": [
            "금감원 검사", "금감원 현장점검", "금감원 제재", "금감원 징계", "금감원 과징금", "금감원 행정처분",
            "금융위 제재", "금융위 과징금", "금융위 의결", "금융위 처분", "제재심", "불완전판매 검사",
            "불완전판매 제재", "위반 적발", "시정명령", "영업정지", "등록취소", "검사 착수", "현장점검",
            "과징금", "행정처분", "징계", "제재", "적발",
        ],
        "weak": ["금융위", "금감원", "금융감독원", "금융위원회", "검사", "위반", "조사", "처분", "의결"],
        "negative": ["금융위기", "간담회", "관계자 발언", "발언", "감독규정 개정안"],
        "demote_to_weak": ["금융위", "금감원", "검사", "민원", "분쟁조정"],
    },
    "입법·정책": {
        "strong": [
            "금융위 제도개선", "금융위 제도 개선", "금융위 방안", "금융위 대책", "금융위 가이드라인", "금융위 입법예고",
            "금융위 시행령", "금융위 시행규칙", "법안", "개정안", "국회 발의", "국회 통과", "정부 정책 발표",
            "금융당국 제도개선", "제도개선", "제도 개선", "입법예고", "시행령", "시행규칙", "가이드라인", "대책",
        ],
        "weak": ["금융위", "금감원", "금융위원회", "금융감독원", "고시", "방안", "정책", "발표", "규정"],
        "negative": ["금융위기", "제재심", "영업정지", "등록취소"],
    },
}

TOPIC_RULE_OVERRIDES: dict[str, dict[str, Any]] = {
    "해외·글로벌": {
        "strong": [
            "연준", "fomc", "ecb", "boj", "뉴욕증시", "나스닥", "다우", "s&p",
            "월가", "미 국채", "미국 국채", "미국채", "미 국채금리", "미국채 금리", "미국 기준금리",
            "미 증시", "미국 증시", "글로벌 채권금리", "글로벌 금융시장",
        ],
        "weak": ["미국", "유럽", "중국", "일본", "영국", "달러", "달러화", "환율", "국채", "국제유가", "글로벌", "해외", "월가"],
        "negative": ["금융위", "금감원", "저축은행", "대부업"],
        "threshold": 3.2,
    },
    "증시·시장시황": {
        "strong": ["뉴욕증시", "코스피", "코스닥", "마감시황", "장 마감", "상승 마감", "하락 마감"],
        "weak": ["증시", "나스닥", "다우", "s&p500", "혼조"],
        "negative": [],
        "threshold": 2.8,
    },
    "환율·외환": {
        "strong": ["원달러", "원/달러", "외환시장", "강달러"],
        "weak": ["환율", "달러", "달러화", "원화"],
        "negative": [],
        "threshold": 2.8,
    },
    "물가·경기지표": {
        "strong": ["cpi", "pce", "소비자물가", "개인소비지출", "인플레이션"],
        "weak": ["경기침체", "경기둔화"],
        "negative": [],
        "threshold": 2.8,
    },
    "금리·수수료·최고금리": {
        "strong": ["최고금리", "기준금리", "금리 산정", "가산금리", "중도상환수수료", "카드수수료", "가맹점 수수료"],
        "weak": ["금리", "수수료"],
        "negative": [],
        "threshold": 2.0,
    },
    "상품·영업·예금금리": {
        "strong": ["예금금리", "수신금리", "대출금리", "마이너스통장", "예대금리차", "특판", "우대금리"],
        "weak": ["신용대출", "주담대", "금융상품"],
        "negative": [],
        "threshold": 2.8,
    },
    "기업금융·익스포저": {
        "strong": ["기업대출", "금융권 익스포저", "익스포저", "워크아웃", "구조조정", "크레딧"],
        "weak": ["회생", "여신", "충당금", "대손"],
        "negative": [],
        "threshold": 2.8,
    },
    "업계동정·사회공헌": {
        "strong": ["금융 브리핑", "금융권 소식", "오늘의 은행", "사회공헌"],
        "weak": [],
        "ignore": ["업무협약", "MOU", "mou", "캠페인", "공모전", "행사", "기부", "후원"],
        "negative": [],
        "threshold": 2.8,
    },
    "일정·브리핑": {
        "strong": ["주요일정", "회의 일정"],
        "weak": ["다음주", "이번주", "일정", "브리핑"],
        "negative": [],
        "threshold": 2.8,
    },
    "칼럼·오피니언": {
        "strong": ["칼럼", "사설", "기고", "기자수첩", "시론", "전문가 진단"],
        "weak": ["데스크"],
        "negative": [],
        "threshold": 2.8,
    },
    "규제·가계부채": {
        "strong": ["가계부채", "대출규제", "총부채"],
        "weak": ["dsr", "ltv", "총부채원리금상환비율", "주택담보인정비율"],
        "negative": [],
        "threshold": 3.6,
    },
    "가계대출·부채": {
        "strong": ["가계대출", "가계부채", "주담대", "주택담보대출", "신용대출", "전세대출", "대출규제", "총부채원리금상환비율"],
        "weak": ["dsr", "ltv"],
        "negative": [],
        "threshold": 3.2,
    },
    "정책·제도개선": {
        "strong": ["제도개선", "제도 개선", "방안", "대책", "가이드라인", "입법예고", "시행령", "시행규칙", "개정안", "법안", "금융위 발표", "금융당국 발표"],
        "weak": ["정책", "발표", "추진", "규정 개정"],
        "negative": [],
        "threshold": 3.4,
    },
    "감독·제재": {
        "strong": ["검사 착수", "현장점검", "제재", "과징금", "징계", "행정처분", "시정명령", "영업정지", "등록취소", "제재심", "단속", "수사", "고발", "적발"],
        "weak": ["검사", "처분", "피해 신고", "불완전판매"],
        "negative": [],
        "threshold": 3.2,
    },
    "건전성·자본규제": {
        "strong": ["k-ics", "킥스", "rbc", "지급여력", "지급여력비율", "자본확충", "건전성", "bis", "보통주자본비율", "자본규제"],
        "weak": ["충당금", "유동성비율", "ncr", "순자본비율"],
        "negative": [],
        "threshold": 3.0,
    },
    "평판·사회이슈": {
        "strong": ["대부업 의혹", "차명 대부업 의혹", "고리대금업 논란", "고리대금업 표현", "사채업자 연루 의혹"],
        "weak": ["논란", "의혹", "공방", "비판", "낙인", "오명", "이미지"],
        "negative": [],
        "threshold": 3.0,
    },
}

TOPIC_CONTEXT_TOKENS: dict[str, dict[str, tuple[str, ...]]] = {
    "해외·글로벌": {
        "title": ("연준", "fomc", "ecb", "boj", "뉴욕증시", "나스닥", "다우", "월가", "미 증시", "미국 증시", "미국채", "미 국채", "글로벌 금융시장"),
        "body": ("미국 기준금리", "글로벌 채권금리", "미 국채금리", "달러화", "국제유가"),
    },
    "증시·시장시황": {
        "title": ("코스피", "코스닥", "뉴욕증시", "나스닥", "다우", "마감시황", "장 마감", "혼조"),
        "body": ("상승 마감", "하락 마감", "증시 변동성"),
    },
    "환율·외환": {
        "title": ("원달러", "원/달러", "외환시장", "강달러", "달러화", "원화"),
        "body": ("달러 강세", "환율 변동성", "외환시장 불안"),
    },
    "물가·경기지표": {
        "title": ("cpi", "pce", "소비자물가", "개인소비지출", "인플레이션", "경기둔화"),
        "body": ("물가 지표", "경기 지표", "경기침체"),
    },
    "금리·수수료·최고금리": {
        "title": ("금리 산정", "가산금리", "우대금리", "최고금리", "중도상환수수료", "보증료", "기준금리"),
        "body": ("금리 체계", "금리 개편", "수수료율", "산정 방식"),
    },
    "상품·영업·예금금리": {
        "title": ("예금금리", "수신금리", "대출금리", "마이너스통장", "예대금리차", "특판", "우대금리"),
        "body": ("금융상품", "영업점", "금리 혜택"),
    },
    "기업금융·익스포저": {
        "title": ("기업대출", "금융권 익스포저", "익스포저", "워크아웃", "구조조정", "크레딧"),
        "body": ("여신 관리", "대손충당금", "기업여신"),
    },
    "연체·부실": {
        "title": ("연체율", "연체", "부실", "고정이하여신", "부실채권", "npl", "장기연체채권", "건전성 악화", "부실 우려"),
        "body": ("건전성", "충당금", "상각", "고정이하", "대손충당금"),
    },
    "부동산·PF": {
        "title": ("부동산 pf", "pf", "프로젝트파이낸싱", "브릿지론", "재구조화"),
        "body": ("미분양", "토지담보", "pf 사업장"),
    },
    "규제·가계부채": {
        "title": ("가계부채", "대출규제", "dsr", "ltv", "총부채"),
        "body": ("총부채원리금상환비율", "주택담보인정비율", "스트레스 dsr", "규제 완화", "규제 강화"),
    },
    "가계대출·부채": {
        "title": ("가계대출", "가계부채", "주담대", "주택담보대출", "신용대출", "dsr", "ltv", "전세대출", "대출규제"),
        "body": ("총부채원리금상환비율", "스트레스 dsr"),
    },
    "불법사금융·불법추심·보이스피싱": {
        "title": ("불법사금융", "불법추심", "보이스피싱", "미등록대부", "피해 확산", "불법사채", "상품권 사채", "특별단속", "고리대금"),
        "body": ("피해구제", "대포통장", "스미싱", "불법 대출광고", "내구제대출", "채권추심"),
    },
    "서민금융·대환·채무조정": {
        "title": ("서민금융", "대환", "채무조정", "햇살론", "신복위", "새도약기금", "캠코", "장기연체채권", "추심중단"),
        "body": ("개인회생", "워크아웃", "출연금", "정책서민금융", "신용회복"),
    },
    "자금시장·유동성": {
        "title": ("자금시장", "유동성", "cp", "abcp", "회사채", "여전채", "카드채", "캐피탈채", "은행채", "채권시장", "차환"),
        "body": ("단기자금", "스프레드", "유동성 지원", "조달금리", "자금조달", "만기", "ncr", "순자본비율"),
    },
    "자산운용·연기금": {
        "title": ("자산운용", "운용사", "연기금", "국민연금"),
        "body": ("etf", "펀드"),
    },
    "디지털자산": {
        "title": ("가상자산", "디지털자산", "암호화폐", "토큰증권", "sto", "업비트", "빗썸", "코빗", "고팍스", "스테이블코인"),
        "body": ("거래소", "코인", "두나무", "fiu", "금융정보분석원"),
    },
    "정책·제도개선": {
        "title": ("제도개선", "제도 개선", "방안", "대책", "정책", "가이드라인", "입법예고", "시행령", "시행규칙", "개정안", "법안"),
        "body": ("규정 개정", "금융투자업규정", "금융위 발표", "금융당국 발표", "추진"),
    },
    "감독·제재": {
        "title": ("검사", "현장점검", "제재", "과징금", "징계", "행정처분", "시정명령", "영업정지", "등록취소", "제재심", "단속", "수사", "고발"),
        "body": ("불완전판매", "적발", "피해 신고"),
    },
    "건전성·자본규제": {
        "title": ("k-ics", "킥스", "rbc", "지급여력", "지급여력비율", "자본확충", "건전성", "bis", "보통주자본비율", "자본규제"),
        "body": ("충당금", "유동성비율", "ncr", "순자본비율"),
    },
    "평판·사회이슈": {
        "title": ("논란", "의혹", "공방", "고리대금업", "이미지", "비판", "낙인", "오명"),
        "body": ("고리대금업 표현",),
    },
}

TOPIC_SECTOR_AFFINITY: dict[str, tuple[str, ...]] = {
    "해외·글로벌": ("거시·시장", "IB·자본시장"),
    "증시·시장시황": ("거시·시장", "IB·자본시장"),
    "환율·외환": ("거시·시장", "IB·자본시장"),
    "물가·경기지표": ("거시·시장",),
    "상품·영업·예금금리": ("은행", "저축은행", "여전", "상호금융"),
    "기업금융·익스포저": ("은행", "IB·자본시장", "저축은행", "여전"),
    "연체·부실": ("대부", "은행", "저축은행", "상호금융", "여전"),
    "부동산·PF": ("저축은행", "IB·자본시장", "여전", "은행"),
    "규제·가계부채": ("은행", "입법·정책", "감독·제재"),
    "불법사금융·불법추심·보이스피싱": ("대부", "감독·제재", "입법·정책"),
    "서민금융·대환·채무조정": ("대부", "은행", "입법·정책", "감독·제재"),
    "자금시장·유동성": ("IB·자본시장", "거시·시장"),
    "자산운용·연기금": ("자산운용·연기금",),
    "디지털자산": ("디지털자산",),
    "정책·제도개선": ("입법·정책", "대부", "은행", "감독·제재"),
    "감독·제재": ("감독·제재", "대부", "은행", "저축은행", "여전"),
    "건전성·자본규제": ("보험", "은행", "저축은행", "상호금융", "여전"),
    "가계대출·부채": ("은행", "입법·정책", "감독·제재"),
    "평판·사회이슈": ("대부",),
}


def _apply_text_alias(text: str, src: str, dst: str) -> str:
    source = normalize_text(src)
    target = normalize_text(dst)
    if source == "美":
        return re.sub(r"美(?=\s*(증시|연준|국채|금리|달러|경제|시장|cpi|pce|fomc))", target, text)
    return text.replace(source, target)


def _normalize_text(text: str) -> str:
    normalized = normalize_text(text)
    for src, dst in TEXT_ALIASES:
        normalized = _apply_text_alias(normalized, src, dst)
    return normalized


def _unique_keep_order(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _build_sector_rules(sector: str, keywords: list[str]) -> dict[str, list[str]]:
    override = SECTOR_RULE_OVERRIDES.get(sector, {})
    demote_to_weak = set(override.get("demote_to_weak", []))
    base_strong = [kw for kw in keywords if kw not in GENERIC_SECTOR_TOKENS and kw not in demote_to_weak]
    strong = _unique_keep_order(base_strong + override.get("strong", []))
    weak = _unique_keep_order([kw for kw in keywords if kw not in strong] + list(demote_to_weak) + override.get("weak", []))
    negative = _unique_keep_order(override.get("negative", []))
    return {
        "strong": strong,
        "weak": weak,
        "generic": [kw for kw in weak if kw in GENERIC_SECTOR_TOKENS],
        "negative": negative,
    }


def _is_schedule_article(text: str) -> bool:
    schedule_patterns = (
        "다음주",
        "이번주",
        "주간",
        "일정",
        "브리핑",
        "회의 일정",
        "주요일정",
    )
    return has_any_term(text, schedule_patterns)


def _has_regulator_anchor(text: str) -> bool:
    regulator_anchors = ("금융위", "금융위원회", "금감원", "금융감독원")
    return has_any_term(text, regulator_anchors)


def _has_supervisory_action(text: str) -> bool:
    strong_signals = (
        "검사 착수",
        "현장점검",
        "개선명령",
        "시정명령",
        "행정처분",
        "불완전판매",
        "중징계",
        "경징계",
        "제재심",
        "과징금",
        "검사",
        "징계",
        "제재",
        "위반",
        "적발",
        "조사",
    )
    return has_any_term(text, strong_signals)


def _has_policy_signal(text: str) -> bool:
    policy_signals = (
        "제도개선",
        "제도 개선",
        "시행령",
        "시행규칙",
        "입법예고",
        "제도 개편",
        "정책 발표",
        "방안 발표",
        "개정",
        "가이드라인",
        "대책",
        "규제 완화",
        "규제 강화",
        "체계 개편",
        "법안",
        "규정",
        "정책",
        "발표",
        "추진",
    )
    return has_any_term(text, policy_signals)


def _has_bank_identity(text: str) -> bool:
    bank_signals = (
        "우리은행",
        "신한은행",
        "국민은행",
        "하나은행",
        "기업은행",
        "농협은행",
        "카카오뱅크",
        "케이뱅크",
        "토스뱅크",
        "은행권",
        "시중은행",
        "인터넷은행",
        "국책은행",
    )
    if has_any_term(text, bank_signals):
        return "투자은행" not in text and "저축은행" not in text and "상호저축은행" not in text
    return False


def _has_market_context(text: str) -> bool:
    market_patterns = (
        "외환시장",
        "금융시장",
        "채권시장",
        "국채금리",
        "국고채",
        "원달러 환율",
        "원/달러",
        "코스피",
        "코스닥",
        "증시",
        "한국은행",
        "한은",
        "기준금리",
        "연준",
        "fomc",
        "시장금리",
        "환율 전망",
        "채권금리",
        "스프레드",
        "마감시황",
        "장 마감",
        "뉴욕증시",
        "나스닥",
        "s&p",
    )
    return has_any_term(text, market_patterns)


def _has_market_title_signal(title_text: str) -> bool:
    return _has_market_context(title_text)


def _has_generic_macro_term(text: str) -> bool:
    generic_terms = ("환율", "금리", "유가", "달러", "원자재")
    return has_any_term(text, generic_terms)


def _has_corporate_earnings_context(text: str) -> bool:
    corporate_terms = (
        "영업이익",
        "매출",
        "순이익",
        "실적",
        "원가",
        "원재료",
        "수출",
        "수주",
        "항공",
        "조선",
        "식품",
        "바이오",
        "게임",
        "자동차",
        "반도체",
        "해운",
        "철강",
    )
    return has_any_term(text, corporate_terms)


def _has_financial_company_context(text: str) -> bool:
    financial_terms = (
        "은행",
        "뱅크",
        "저축은행",
        "보험",
        "보험사",
        "카드",
        "카드사",
        "카드론",
        "캐피탈",
        "여전",
        "증권",
        "증권사",
        "자산운용",
        "운용사",
        "대부업",
        "상호금융",
        "새마을금고",
        "신협",
        "금융권",
        "금융회사",
        "핀테크",
    )
    return has_any_term(text, financial_terms)


def _has_capital_market_anchor(text: str) -> bool:
    anchors = (
        "ipo 주관",
        "상장 주관",
        "공모주 주관",
        "주관사",
        "인수단",
        "증권사",
        "투자은행",
        "ecm",
        "dcm",
        "회사채 발행",
        "채권발행시장",
        "cp시장",
        "abcp",
        "공모주",
    )
    return has_any_term(text, anchors)


def _has_generic_ipo_only(text: str) -> bool:
    return has_any_term(text, ("ipo", "상장")) and not _has_capital_market_anchor(text)


def _has_bank_quote_source_signal(text: str) -> bool:
    quote_patterns = (
        "딜링룸",
        "연구원",
        "관계자",
        "증권가",
        "시장 참가자",
        "트레이더",
        "외환 딜러",
    )
    return has_any_term(text, quote_patterns)


def _has_explicit_bank_brand(text: str) -> bool:
    explicit_bank_brands = (
        "우리은행",
        "신한은행",
        "국민은행",
        "하나은행",
        "ibk기업은행",
        "기업은행",
        "nh농협은행",
        "농협은행",
        "sc제일은행",
        "씨티은행",
        "산업은행",
        "수출입은행",
        "부산은행",
        "경남은행",
        "광주은행",
        "전북은행",
        "대구은행",
        "카카오뱅크",
        "케이뱅크",
        "토스뱅크",
    )
    return has_any_term(text, explicit_bank_brands)


def _has_non_financial_loan_lease_context(text: str) -> bool:
    return has_any_term(
        text,
        (
            "공유재산 대부",
            "공유재산 대부계약",
            "지자체 공유재산 대부",
            "국유재산 대부료",
            "토지 대부",
            "농지 대부",
            "공공시설 대부",
            "대부계약",
            "무상대부",
            "대부도",
        ),
    )


def _has_explicit_loan_business_anchor(text: str) -> bool:
    if has_any_term(
        text,
        (
            "대부업", "대부업권", "대부업계", "대부업체", "대부업자", "대부중개", "대부중개업", "대부업법",
            "등록대부업체", "미등록대부", "불법대부", "불법사금융", "불법사채", "사채업자", "고리대금", "고리대금업",
            "상품권 사채", "내구제대출", "내구제 대출", "불법추심", "채권추심", "대부채권",
        ),
    ):
        return True
    has_benefit_debt_anchor = has_any_term(text, ("새도약기금", "장기연체채권", "추심중단"))
    has_loan_business_context = has_any_term(text, ("대부업계", "대부업권", "대부업체", "대부업", "캠코"))
    return has_benefit_debt_anchor and has_loan_business_context


@dataclass(frozen=True)
class _AdjustmentWeights:
    """섹터 점수 보정 가중치.

    전체점수 보정(_SECTOR_ADJUSTMENT_WEIGHTS)과 제목점수 보정(_TITLE_BIAS_WEIGHTS)은
    로직이 완전히 같고 숫자와 IPO 판정 범위만 다르다.
    """

    bank_subject_boost: float
    bank_desc_boost: float
    bank_quote_penalty: float
    market_title_macro_boost: float
    market_title_bank_penalty: float
    generic_macro_corporate_penalty: float
    generic_macro_financial_penalty: float
    generic_macro_default_penalty: float
    generic_ipo_penalty: float
    ipo_scope_title_only: bool  # IPO 노이즈 판정을 제목만으로 할지(제목점수 보정) 여부
    schedule_penalty: float
    policy_legislation_boost: float
    policy_macro_penalty: float
    supervision_action_boost: float
    supervision_no_action_penalty: float
    supervision_no_regulator_penalty: float
    loan_business_boost: float


_SECTOR_ADJUSTMENT_WEIGHTS = _AdjustmentWeights(
    bank_subject_boost=5.0,
    bank_desc_boost=1.5,
    bank_quote_penalty=2.5,
    market_title_macro_boost=5.0,
    market_title_bank_penalty=4.0,
    generic_macro_corporate_penalty=10.0,
    generic_macro_financial_penalty=8.0,
    generic_macro_default_penalty=5.0,
    generic_ipo_penalty=6.0,
    ipo_scope_title_only=False,
    schedule_penalty=1.2,
    policy_legislation_boost=4.0,
    policy_macro_penalty=2.0,
    supervision_action_boost=8.0,
    supervision_no_action_penalty=4.0,
    supervision_no_regulator_penalty=10.0,
    loan_business_boost=14.0,
)

_TITLE_BIAS_WEIGHTS = _AdjustmentWeights(
    bank_subject_boost=7.0,
    bank_desc_boost=1.0,
    bank_quote_penalty=2.0,
    market_title_macro_boost=6.0,
    market_title_bank_penalty=3.0,
    generic_macro_corporate_penalty=12.0,
    generic_macro_financial_penalty=9.0,
    generic_macro_default_penalty=7.0,
    generic_ipo_penalty=7.0,
    ipo_scope_title_only=True,
    schedule_penalty=1.0,
    policy_legislation_boost=3.0,
    policy_macro_penalty=1.0,
    supervision_action_boost=12.0,
    supervision_no_action_penalty=5.0,
    supervision_no_regulator_penalty=12.0,
    loan_business_boost=16.0,
)


def _apply_score_adjustments(
    title_text: str,
    desc_text: str,
    scores: dict[str, int],
    weights: _AdjustmentWeights,
) -> dict[str, float]:
    adjusted: dict[str, float] = {k: float(v) for k, v in scores.items()}
    configured = set(adjusted)
    combined = f"{title_text} {desc_text}".strip()
    has_schedule = _is_schedule_article(combined)
    has_action = _has_supervisory_action(combined)
    has_regulator = _has_regulator_anchor(combined)
    has_policy = _has_policy_signal(combined)
    has_market_context = _has_market_context(combined)
    has_generic_macro = _has_generic_macro_term(combined)
    has_corporate_context = _has_corporate_earnings_context(combined)
    has_financial_context = _has_financial_company_context(combined)
    has_bank_title = _has_bank_identity(title_text)
    has_bank_desc = _has_bank_identity(desc_text)
    has_market_title = _has_market_title_signal(title_text)
    has_bank_quote_source = _has_bank_quote_source_signal(combined)
    has_explicit_bank_title = _has_explicit_bank_brand(title_text)
    has_bank_subject_title = has_bank_title and (has_explicit_bank_title or not (has_market_title and _has_bank_quote_source_signal(title_text)))
    has_loan_business_anchor = _has_explicit_loan_business_anchor(combined)
    has_non_financial_lease = _has_non_financial_loan_lease_context(combined)

    if "은행" in configured:
        if has_bank_subject_title:
            adjusted["은행"] += weights.bank_subject_boost
        elif has_bank_desc:
            adjusted["은행"] += weights.bank_desc_boost
        if has_bank_quote_source:
            adjusted["은행"] -= weights.bank_quote_penalty
    if has_market_title and "거시·시장" in configured:
        adjusted["거시·시장"] += weights.market_title_macro_boost
        if "은행" in configured and (has_bank_quote_source or has_bank_desc) and not has_bank_subject_title:
            adjusted["은행"] -= weights.market_title_bank_penalty
    if "거시·시장" in configured and has_generic_macro and not has_market_context:
        if has_corporate_context and not has_financial_context:
            adjusted["거시·시장"] -= weights.generic_macro_corporate_penalty
        elif has_financial_context:
            adjusted["거시·시장"] -= weights.generic_macro_financial_penalty
        else:
            adjusted["거시·시장"] -= weights.generic_macro_default_penalty
    ipo_scope_text = title_text if weights.ipo_scope_title_only else combined
    if "IB·자본시장" in configured and _has_generic_ipo_only(ipo_scope_text):
        adjusted["IB·자본시장"] -= weights.generic_ipo_penalty

    if has_schedule:
        for sector in ("감독·제재", "입법·정책", "거시·시장"):
            if sector in configured:
                adjusted[sector] -= weights.schedule_penalty
        if not has_action and "감독·제재" in configured:
            adjusted["감독·제재"] -= 2.0

    if has_policy:
        if "입법·정책" in configured:
            adjusted["입법·정책"] += weights.policy_legislation_boost
        if "거시·시장" in configured:
            adjusted["거시·시장"] -= weights.policy_macro_penalty
        if "감독·제재" in configured:
            adjusted["감독·제재"] -= 3.0 if not has_action else 1.0

    if "감독·제재" in configured:
        if has_regulator and has_action and not has_policy:
            adjusted["감독·제재"] += weights.supervision_action_boost
        elif has_regulator and not has_action:
            adjusted["감독·제재"] -= weights.supervision_no_action_penalty
        elif not has_regulator:
            adjusted["감독·제재"] -= weights.supervision_no_regulator_penalty

    if "대부" in configured and has_loan_business_anchor and not has_non_financial_lease:
        adjusted["대부"] += weights.loan_business_boost
        for sector in ("입법·정책", "감독·제재", "은행", "거시·시장"):
            if sector in configured:
                adjusted[sector] -= 4.0

    return adjusted


def _apply_sector_adjustments(
    title_text: str,
    desc_text: str,
    sector_scores: dict[str, int],
) -> dict[str, float]:
    return _apply_score_adjustments(title_text, desc_text, sector_scores, _SECTOR_ADJUSTMENT_WEIGHTS)


def _apply_title_biases(
    title_text: str,
    desc_text: str,
    title_scores: dict[str, int],
) -> dict[str, float]:
    return _apply_score_adjustments(title_text, desc_text, title_scores, _TITLE_BIAS_WEIGHTS)


def _score_sector(title_text: str, desc_text: str, rules: dict[str, list[str]]) -> tuple[int, int, list[str]]:
    title_strong_hits = _collect_hits(rules["strong"], title_text)
    desc_strong_hits = _collect_hits(rules["strong"], desc_text)
    title_weak_hits = _collect_hits(rules["weak"], title_text)
    desc_weak_hits = _collect_hits(rules["weak"], desc_text)
    title_generic_hits = _collect_hits(rules["generic"], title_text)
    desc_generic_hits = _collect_hits(rules["generic"], desc_text)
    title_negative_hits = _collect_hits(rules["negative"], title_text)
    desc_negative_hits = _collect_hits(rules["negative"], desc_text)

    title_score = (
        len(title_strong_hits) * TITLE_STRONG_SCORE
        + len(
            [
                kw
                for kw in title_weak_hits
                if kw not in title_strong_hits and kw not in title_generic_hits
            ]
        )
        * TITLE_WEAK_SCORE
        + len(title_generic_hits) * TITLE_GENERIC_SCORE
        - len(title_negative_hits) * TITLE_NEGATIVE_PENALTY
    )
    body_score = (
        len([kw for kw in desc_strong_hits if kw not in title_strong_hits]) * BODY_STRONG_SCORE
        + len(
            [
                kw
                for kw in desc_weak_hits
                if kw not in title_weak_hits and kw not in desc_generic_hits
            ]
        )
        * BODY_WEAK_SCORE
        + len([kw for kw in desc_generic_hits if kw not in title_generic_hits]) * BODY_GENERIC_SCORE
        - len(desc_negative_hits) * BODY_NEGATIVE_PENALTY
    )
    score = title_score + body_score

    positive_hits = _unique_keep_order(
        [
            *title_strong_hits,
            *title_weak_hits,
            *desc_strong_hits,
            *desc_weak_hits,
        ]
    )
    return score, title_score, positive_hits


def _build_topic_rules(topic: str, keywords: list[str]) -> dict[str, Any]:
    override = TOPIC_RULE_OVERRIDES.get(topic, {})
    strong = _unique_keep_order(override.get("strong", []))
    ignored = set(override.get("ignore", []))
    weak = _unique_keep_order([kw for kw in keywords if kw not in strong] + override.get("weak", []))
    if ignored:
        weak = [kw for kw in weak if kw not in ignored]
    negative = _unique_keep_order(override.get("negative", []))
    threshold = float(override.get("threshold", DEFAULT_TOPIC_THRESHOLD))
    return {
        "strong": strong or keywords,
        "weak": weak,
        "negative": negative,
        "threshold": threshold,
    }


def _score_topic(
    title_text: str,
    body_text: str,
    query_text: str,
    sector: str,
    topic: str,
    rules: dict[str, Any],
) -> tuple[float, list[str]]:
    combined_text = f"{title_text} {body_text}".strip()
    title_strong_hits = _collect_hits(rules["strong"], title_text)
    body_strong_hits = _collect_hits(rules["strong"], body_text)
    title_weak_hits = _collect_hits(rules["weak"], title_text)
    body_weak_hits = _collect_hits(rules["weak"], body_text)
    title_negative_hits = _collect_hits(rules["negative"], title_text)
    body_negative_hits = _collect_hits(rules["negative"], body_text)

    content_hits = {
        *title_strong_hits,
        *body_strong_hits,
        *title_weak_hits,
        *body_weak_hits,
    }
    query_hits = [
        kw
        for kw in _collect_hits([*rules["strong"], *rules["weak"]], query_text)
        if kw not in content_hits
    ]

    context = TOPIC_CONTEXT_TOKENS.get(topic, {})
    title_context_hits = _collect_hits(list(context.get("title", ())), title_text)
    body_context_hits = _collect_hits(list(context.get("body", ())), body_text)
    has_sector_affinity = sector in TOPIC_SECTOR_AFFINITY.get(topic, ())

    score = (
        len(title_strong_hits) * TOPIC_TITLE_STRONG_SCORE
        + len([kw for kw in body_strong_hits if kw not in title_strong_hits]) * TOPIC_BODY_STRONG_SCORE
        + len([kw for kw in title_weak_hits if kw not in title_strong_hits]) * TOPIC_TITLE_WEAK_SCORE
        + len(
            [
                kw
                for kw in body_weak_hits
                if kw not in title_weak_hits and kw not in body_strong_hits and kw not in title_strong_hits
            ]
        )
        * TOPIC_BODY_WEAK_SCORE
        + len([kw for kw in title_context_hits if kw not in title_strong_hits and kw not in title_weak_hits])
        * TOPIC_TITLE_CONTEXT_SCORE
        + len([kw for kw in body_context_hits if kw not in body_strong_hits and kw not in body_weak_hits])
        * TOPIC_BODY_CONTEXT_SCORE
        + (TOPIC_SECTOR_AUX_SCORE if has_sector_affinity and (title_strong_hits or title_weak_hits or title_context_hits) else 0.0)
        + len(query_hits) * TOPIC_QUERY_AUX_SCORE
        - len(title_negative_hits) * TOPIC_TITLE_NEGATIVE_PENALTY
        - len(body_negative_hits) * TOPIC_BODY_NEGATIVE_PENALTY
    )
    if topic == "평판·사회이슈":
        has_rep_term = has_any_term(combined_text, ("논란", "의혹", "공방", "고리대금업", "비판", "낙인", "오명", "이미지"))
        has_finance_anchor = _has_explicit_loan_business_anchor(combined_text) or has_any_term(combined_text, ("금융", "업권", "대부", "사채"))
        if has_rep_term and not has_finance_anchor:
            score -= 6.0
    if topic == "연체·부실":
        has_only_generic_bad_debt = has_any_term(combined_text, ("부실", "부실 우려")) and not has_any_term(
            combined_text,
            ("연체", "연체율", "부실채권", "npl", "고정이하여신", "충당금", "저축은행", "은행", "금고", "카드", "대부업", "금융", "pf"),
        )
        if has_only_generic_bad_debt:
            score -= 4.0
    hits = _unique_keep_order(
        [*title_strong_hits, *title_weak_hits, *body_strong_hits, *body_weak_hits, *title_context_hits, *body_context_hits]
    )
    return score, hits


def _keyword_in_text(keyword: str, text: str) -> bool:
    kw = (keyword or "").strip()
    if not kw:
        return False
    if kw.lower() == "리스":
        return contains_term(text, kw, exclude_terms=["리스크"], mode="phrase")
    if kw in RISKY_SHORT_KEYWORDS or kw.lower() in {"은행", "거래소", "펀드", "대출", "보험"}:
        return contains_term(text, kw, mode="token")
    return contains_term(text, kw)


def _collect_hits(keywords: list[str], text: str) -> list[str]:
    return [kw for kw in keywords if _keyword_in_text(kw, text)]


def _apply_topic_fallbacks(
    *,
    title_text: str,
    body_text: str,
    query_text: str,
    sector: str,
    topics: list[str],
) -> list[str]:
    fallback_topics = list(topics)
    content_text = " ".join(text for text in (title_text, body_text) if text).strip()

    def append_topic(topic: str) -> None:
        if topic not in fallback_topics:
            fallback_topics.append(topic)

    def has_overseas_anchor() -> bool:
        return has_any_term(
            content_text,
            (
                "미국", "미 증시", "미국 증시", "美", "연준", "fomc", "ecb", "boj", "뉴욕증시",
                "나스닥", "다우", "s&p", "s&p500", "월가", "미 국채", "미국 국채", "미국채",
                "미국 기준금리", "글로벌 금융시장", "글로벌 채권금리",
            ),
        )

    def has_domestic_anchor() -> bool:
        return has_any_term(
            content_text,
            (
                "국내", "한국", "한은", "한국은행", "통계청", "국내증시", "코스피", "코스닥",
                "원화", "원달러", "원/달러",
            ),
        )

    def has_financial_activity_anchor() -> bool:
        return has_any_term(
            content_text,
            (
                "은행", "은행권", "금융권", "금융회사", "저축은행", "보험", "보험사", "카드",
                "카드사", "캐피탈", "증권", "증권사", "신협", "새마을금고", "상호금융",
                "농협은행", "신한은행", "국민은행", "우리은행", "하나은행", "기업은행",
                "카카오뱅크", "케이뱅크", "토스뱅크",
            ),
        )

    if sector == "거시·시장":
        if has_overseas_anchor() and not (
            has_domestic_anchor()
            and not has_any_term(content_text, ("미국", "美", "연준", "fomc", "ecb", "boj", "뉴욕증시", "나스닥", "다우", "s&p", "월가"))
        ):
            append_topic("해외·글로벌")
        if has_any_term(
            content_text,
            ("원달러", "원/달러", "환율", "외환시장", "달러", "달러화", "강달러", "원화"),
        ):
            append_topic("환율·외환")
        if has_any_term(
            content_text,
            (
                "코스피", "코스닥", "증시", "뉴욕증시", "나스닥", "다우", "s&p", "s&p500",
                "마감시황", "장 마감", "상승 마감", "하락 마감", "혼조",
            ),
        ):
            append_topic("증시·시장시황")

    if sector in ("은행", "저축은행", "여전", "상호금융") and has_any_term(
        content_text,
        (
            "예금금리", "수신금리", "대출금리", "마이너스통장", "신용대출", "주담대", "예대금리차",
            "특판", "우대금리",
        ),
    ):
        append_topic("상품·영업·예금금리")

    if sector in ("은행", "IB·자본시장", "저축은행", "여전") and has_any_term(
        content_text,
        ("기업대출", "금융권 익스포저", "익스포저", "회생", "워크아웃", "구조조정", "여신", "크레딧", "대손"),
    ):
        append_topic("기업금융·익스포저")

    if has_any_term(content_text, ("다음주", "이번주", "주요일정", "회의 일정")):
        append_topic("일정·브리핑")
    if has_any_term(content_text, ("금융 브리핑", "오늘의 은행", "금융권 소식", "사회공헌")) or (
        has_financial_activity_anchor()
        and has_any_term(content_text, ("업무협약", "mou", "캠페인", "공모전", "행사", "기부", "후원"))
    ):
        append_topic("업계동정·사회공헌")
    if has_any_term(content_text, ("칼럼", "사설", "기고", "기자수첩", "시론", "전문가 진단")):
        append_topic("칼럼·오피니언")

    return fallback_topics


def tag_articles(
    articles: list[Article],
    sector_queries: dict[str, list[str]],
    topic_queries: dict[str, list[str]] | None = None,
) -> list[TaggedArticle]:
    topic_queries = topic_queries or {}

    # 규칙 빌드는 기사와 무관하므로 루프 밖에서 1회만 수행한다.
    sector_rules_map = {
        sector: _build_sector_rules(sector, keywords)
        for sector, keywords in sector_queries.items()
    }
    topic_rules_map = {
        topic: _build_topic_rules(topic, keywords)
        for topic, keywords in topic_queries.items()
    }

    tagged: list[TaggedArticle] = []
    for article in articles:
        title_text = _normalize_text((article.title or "").lower())
        desc_text = _normalize_text((article.description or "").lower())
        query_text = _normalize_text((getattr(article, "query", "") or "").lower())
        body_sources = [
            desc_text,
            _normalize_text((getattr(article, "summary", "") or "").lower()),
            _normalize_text((getattr(article, "full_text", "") or "").lower()),
            _normalize_text((getattr(article, "main_text", "") or "").lower()),
            _normalize_text((getattr(article, "content", "") or "").lower()),
        ]
        body_text = " ".join(x for x in body_sources if x).strip()

        # -----------------------
        # Sector: best 1 (제목/요약 기반)
        # -----------------------
        best_sector = "기타"
        best_score = float("-inf")
        best_title_score = float("-inf")
        best_hits: list[str] = []
        sector_scores: dict[str, int] = {}
        sector_title_scores: dict[str, int] = {}
        sector_hits: dict[str, list[str]] = {}
        for sector, rules in sector_rules_map.items():
            score, title_score, hits = _score_sector(title_text, desc_text, rules)
            sector_scores[sector] = score
            sector_title_scores[sector] = title_score
            sector_hits[sector] = hits

        adjusted_scores = _apply_sector_adjustments(title_text, desc_text, sector_scores)
        adjusted_title_scores = _apply_title_biases(title_text, desc_text, sector_title_scores)
        for sector, score in adjusted_scores.items():
            title_score = adjusted_title_scores.get(sector, 0.0)
            if (title_score, score) > (best_title_score, best_score):
                best_score = score
                best_title_score = title_score
                best_sector = sector
                best_hits = sector_hits.get(sector, [])

        sectors = [best_sector] if best_score >= PRIMARY_SECTOR_THRESHOLD else ["기타"]

        # -----------------------
        # Topics: multi
        # -----------------------
        topics: list[str] = []
        topic_hits_all: list[str] = []
        for topic, rules in topic_rules_map.items():
            score, hits = _score_topic(title_text, body_text, query_text, best_sector, topic, rules)
            if score >= rules["threshold"] and hits:
                topics.append(topic)
                topic_hits_all.extend(hits)
        if topic_queries:
            topics = [
                topic
                for topic in _apply_topic_fallbacks(
                    title_text=title_text,
                    body_text=body_text,
                    query_text=query_text,
                    sector=sectors[0],
                    topics=topics,
                )
                if topic in topic_queries
            ]

        matched_keywords = list(dict.fromkeys([*best_hits, *topic_hits_all]))

        tagged.append(
            TaggedArticle(
                article=article,
                sectors=sectors,
                topics=topics,
                matched_keywords=matched_keywords,
            )
        )

    return tagged


def keyword_trends(tagged: list[TaggedArticle], top_n: int = 10) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter()
    for item in tagged:
        counter.update(item.matched_keywords)
    return counter.most_common(top_n)
