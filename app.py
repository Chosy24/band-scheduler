from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional, Sequence, Set, Tuple

import requests
import streamlit as st

PARTS = [
    "보컬",
    "기타1",
    "기타2",
    "베이스",
    "드럼",
    "건반1",
    "건반2",
    "기타/그 외",
]


@dataclass
class SchedulePeriod:
    id: str
    name: str
    start_date: date
    end_date: date
    start_time: time
    end_time: time
    slot_minutes: int


@dataclass
class Member:
    id: str
    name: str
    parts: List[str]
    active: bool = True


@dataclass
class Team:
    id: str
    schedule_id: str
    name: str
    songs: str
    member_roles: Dict[str, str]

    @property
    def member_ids(self) -> List[str]:
        return list(self.member_roles.keys())


@dataclass
class PersonalSchedule:
    schedule_id: str
    member_id: str
    selected_slots: List[str]
    submitted_at: str = ""


# =========================
# Google Apps Script API
# =========================
def get_gas_url() -> str:
    try:
        return str(st.secrets.get("GAS_WEB_APP_URL", "")).strip()
    except Exception:
        return ""


def get_admin_pin() -> str:
    try:
        return str(st.secrets.get("ADMIN_PIN", "")).strip()
    except Exception:
        return ""


@st.cache_resource
def get_http_session() -> requests.Session:
    """HTTP 연결을 재사용해서 Apps Script 왕복 비용을 줄인다."""
    return requests.Session()


@st.cache_data(ttl=45, show_spinner=False)
def _cached_api_get(url: str, action: str, params_json: str) -> Dict[str, object]:
    """GET 결과를 잠깐 캐시해 Streamlit 재실행 때 중복 조회를 막는다."""
    query = {"action": action}
    if params_json:
        query.update(json.loads(params_json))

    response = get_http_session().get(url, params=query, timeout=20)
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(str(data.get("error", "Google Apps Script API 오류")))
    return data


def api_get(action: str, params: Optional[Dict[str, str]] = None) -> Dict[str, object]:
    url = get_gas_url()
    if not url:
        raise RuntimeError("GAS_WEB_APP_URL이 설정되지 않았습니다.")

    params_json = json.dumps(params or {}, ensure_ascii=False, sort_keys=True)
    return _cached_api_get(url, action, params_json)


def api_post(payload: Dict[str, object]) -> Dict[str, object]:
    url = get_gas_url()
    if not url:
        raise RuntimeError("GAS_WEB_APP_URL이 설정되지 않았습니다.")

    response = get_http_session().post(url, json=payload, timeout=20)
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(str(data.get("error", "Google Apps Script API 오류")))

    # 저장/삭제 후에는 다음 조회가 최신 데이터를 가져오도록 GET 캐시만 비운다.
    _cached_api_get.clear()
    return data


# =========================
# Parsing helpers
# =========================
def parse_date(value: object) -> date:
    text = str(value).strip().split("T", 1)[0]
    return datetime.strptime(text, "%Y-%m-%d").date()


def parse_time(value: object) -> time:
    text = str(value).strip()
    if "T" in text:
        text = text.split("T", 1)[1]
    return datetime.strptime(text[:5], "%H:%M").time()


def parse_json_list(value: object) -> List[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    text = str(value or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        return [str(v) for v in parsed] if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []


def parse_json_dict(value: object) -> Dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return {str(k): str(v) for k, v in parsed.items()}
    except json.JSONDecodeError:
        pass
    return {}


def normalize_display_name(name: str) -> str:
    return " ".join(name.strip().split())


def normalize_name_key(name: str) -> str:
    return "".join(normalize_display_name(name).split()).lower()


def row_to_period(row: Dict[str, object]) -> SchedulePeriod:
    return SchedulePeriod(
        id=str(row.get("schedule_id", "")).strip(),
        name=str(row.get("name", "")).strip(),
        start_date=parse_date(row.get("start_date", date.today().isoformat())),
        end_date=parse_date(row.get("end_date", date.today().isoformat())),
        start_time=parse_time(row.get("start_time", "18:00")),
        end_time=parse_time(row.get("end_time", "22:00")),
        slot_minutes=int(float(row.get("slot_minutes", 60) or 60)),
    )


def row_to_member(row: Dict[str, object]) -> Member:
    active = str(row.get("active", True)).lower() not in {"false", "0", "no", "n"}
    parts = parse_json_list(row.get("parts", "[]"))

    # 예전 v2 초안의 단일 part 컬럼과 호환
    if not parts:
        legacy_part = str(row.get("part", "")).strip()
        if legacy_part:
            parts = [legacy_part]

    return Member(
        id=str(row.get("member_id", "")).strip(),
        name=str(row.get("name", "")).strip(),
        parts=parts or ["기타/그 외"],
        active=active,
    )


def row_to_team(row: Dict[str, object]) -> Team:
    roles = parse_json_dict(row.get("member_roles", "{}"))

    # 예전 v2 초안의 member_ids 데이터와 호환
    if not roles:
        for member_id in parse_json_list(row.get("member_ids", "[]")):
            roles[member_id] = ""

    return Team(
        id=str(row.get("team_id", "")).strip(),
        schedule_id=str(row.get("schedule_id", "")).strip(),
        name=str(row.get("name", "")).strip(),
        songs=str(row.get("songs", "")).strip(),
        member_roles=roles,
    )


def row_to_schedule(row: Dict[str, object]) -> PersonalSchedule:
    return PersonalSchedule(
        schedule_id=str(row.get("schedule_id", "")).strip(),
        member_id=str(row.get("member_id", "")).strip(),
        selected_slots=parse_json_list(row.get("selected_slots", "[]")),
        submitted_at=str(row.get("submitted_at", "")),
    )


# =========================
# Data access
# =========================
def get_current_period() -> Optional[SchedulePeriod]:
    data = api_get("getSettings")
    row = data.get("settings")
    if not isinstance(row, dict) or not row.get("schedule_id"):
        return None
    return row_to_period(row)


def save_period(
    name: str,
    start_date: date,
    end_date: date,
    start_time: time,
    end_time: time,
    slot_minutes: int,
) -> str:
    data = api_post({
        "action": "saveSettings",
        "name": name.strip(),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "start_time": start_time.strftime("%H:%M"),
        "end_time": end_time.strftime("%H:%M"),
        "slot_minutes": int(slot_minutes),
    })
    return str(data["schedule_id"])


def list_members(active_only: bool = True) -> List[Member]:
    data = api_get(
        "getMembers",
        {"active_only": "true" if active_only else "false"},
    )
    rows = data.get("members", [])
    if not isinstance(rows, list):
        return []
    return [row_to_member(row) for row in rows if isinstance(row, dict)]


def save_member_and_schedule(
    schedule_id: str,
    name: str,
    parts: Sequence[str],
    selected_slots: Sequence[str],
) -> Member:
    data = api_post({
        "action": "saveMemberAndSchedule",
        "schedule_id": schedule_id,
        "name": normalize_display_name(name),
        "parts": list(parts),
        "selected_slots": list(selected_slots),
    })
    row = data.get("member")
    if not isinstance(row, dict):
        raise RuntimeError("저장된 부원 정보를 받지 못했습니다.")
    return row_to_member(row)


def get_personal_schedule(schedule_id: str, member_id: str) -> Optional[PersonalSchedule]:
    data = api_get(
        "getMemberSchedule",
        {"schedule_id": schedule_id, "member_id": member_id},
    )
    row = data.get("schedule")
    return row_to_schedule(row) if isinstance(row, dict) else None


def create_team(
    schedule_id: str,
    name: str,
    songs: str,
    member_roles: Dict[str, str],
) -> str:
    data = api_post({
        "action": "createTeam",
        "schedule_id": schedule_id,
        "name": name.strip(),
        "songs": songs.strip(),
        "member_roles": member_roles,
        # 기존 V2_Teams 시트 호환용
        "member_ids": list(member_roles.keys()),
    })
    return str(data["team_id"])


def delete_team(team_id: str) -> None:
    api_post({"action": "deleteTeam", "team_id": team_id})


def list_teams(schedule_id: Optional[str] = None) -> List[Team]:
    params = {"schedule_id": schedule_id} if schedule_id else None
    data = api_get("getTeams", params)
    rows = data.get("teams", [])
    if not isinstance(rows, list):
        return []
    return [row_to_team(row) for row in rows if isinstance(row, dict)]


def get_team_bundle(
    team_id: str,
) -> Tuple[Optional[Team], List[Member], Dict[str, PersonalSchedule]]:
    data = api_get("getTeamBundle", {"team_id": team_id})
    team_row = data.get("team")
    if not isinstance(team_row, dict):
        return None, [], {}

    team = row_to_team(team_row)
    members_raw = data.get("members", [])
    schedules_raw = data.get("schedules", [])

    members = (
        [row_to_member(r) for r in members_raw if isinstance(r, dict)]
        if isinstance(members_raw, list)
        else []
    )
    schedule_list = (
        [row_to_schedule(r) for r in schedules_raw if isinstance(r, dict)]
        if isinstance(schedules_raw, list)
        else []
    )
    schedules = {s.member_id: s for s in schedule_list}

    # 예전 팀 데이터에 담당 파트가 없을 때 첫 번째 등록 파트 사용
    member_by_id = {m.id: m for m in members}
    for member_id in team.member_ids:
        if not team.member_roles.get(member_id) and member_id in member_by_id:
            team.member_roles[member_id] = member_by_id[member_id].parts[0]

    return team, members, schedules


def save_final_schedule(
    schedule_id: str,
    team_id: str,
    start_dt: datetime,
    end_dt: datetime,
) -> None:
    api_post({
        "action": "saveFinalSchedule",
        "schedule_id": schedule_id,
        "team_id": team_id,
        "start_dt": start_dt.isoformat(timespec="minutes"),
        "end_dt": end_dt.isoformat(timespec="minutes"),
    })


def list_final_schedules(schedule_id: str) -> List[Dict[str, object]]:
    data = api_get("getFinalSchedules", {"schedule_id": schedule_id})
    rows = data.get("final_schedules", [])
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def delete_final_schedule(final_id: str) -> None:
    api_post({"action": "deleteFinalSchedule", "final_id": final_id})


# =========================
# Schedule calculations
# =========================
def weekday_kr(d: date) -> str:
    return ["월", "화", "수", "목", "금", "토", "일"][d.weekday()]


def generate_slots(period: SchedulePeriod) -> List[Dict[str, object]]:
    slots: List[Dict[str, object]] = []
    current_date = period.start_date

    while current_date <= period.end_date:
        current = datetime.combine(current_date, period.start_time)
        end_dt = datetime.combine(current_date, period.end_time)

        while current + timedelta(minutes=period.slot_minutes) <= end_dt:
            nxt = current + timedelta(minutes=period.slot_minutes)
            slots.append({
                "id": current.isoformat(timespec="minutes"),
                "date": current_date,
                "date_label": f"{current_date.strftime('%m/%d')}({weekday_kr(current_date)})",
                "start": current,
                "end": nxt,
                "time_label": f"{current.strftime('%H:%M')}-{nxt.strftime('%H:%M')}",
            })
            current = nxt

        current_date += timedelta(days=1)

    return slots


def build_slot_maps(
    period: SchedulePeriod,
) -> Tuple[List[str], List[str], Dict[Tuple[str, str], str]]:
    date_labels: List[str] = []
    time_labels: List[str] = []
    slot_map: Dict[Tuple[str, str], str] = {}

    for slot in generate_slots(period):
        date_label = str(slot["date_label"])
        time_label = str(slot["time_label"])
        if date_label not in date_labels:
            date_labels.append(date_label)
        if time_label not in time_labels:
            time_labels.append(time_label)
        slot_map[(date_label, time_label)] = str(slot["id"])

    return date_labels, time_labels, slot_map


def analyze_team_slots(
    period: SchedulePeriod,
    team: Team,
    members: Sequence[Member],
    schedules: Dict[str, PersonalSchedule],
) -> List[Dict[str, object]]:
    member_by_id = {m.id: m for m in members}
    team_member_ids = [mid for mid in team.member_ids if mid in member_by_id]

    selected_sets: Dict[str, Set[str]] = {
        mid: set(schedules[mid].selected_slots) if mid in schedules else set()
        for mid in team_member_ids
    }

    rows: List[Dict[str, object]] = []

    for slot in generate_slots(period):
        slot_id = str(slot["id"])
        available_ids = [mid for mid in team_member_ids if slot_id in selected_sets[mid]]
        unavailable_ids = [mid for mid in team_member_ids if mid not in available_ids]

        unavailable_names = []
        for mid in unavailable_ids:
            member = member_by_id[mid]
            role = team.member_roles.get(mid, "") or member.parts[0]
            unavailable_names.append(f"{member.name}({role})")

        rows.append({
            "slot_id": slot_id,
            "date": slot["date"],
            "date_label": slot["date_label"],
            "start": slot["start"],
            "end": slot["end"],
            "time_label": slot["time_label"],
            "available_count": len(available_ids),
            "total_count": len(team_member_ids),
            "unavailable_ids": unavailable_ids,
            "unavailable_names": unavailable_names,
        })

    return rows


def group_rehearsal_blocks(
    rows: Sequence[Dict[str, object]],
    max_absent: int,
) -> List[Dict[str, object]]:
    """같은 날짜 + 같은 불참자 구성의 연속 슬롯을 한 덩어리로 합친다."""
    eligible = [
        row
        for row in rows
        if len(row["unavailable_ids"]) <= max_absent
    ]

    if not eligible:
        return []

    blocks: List[Dict[str, object]] = []
    current: Optional[Dict[str, object]] = None

    for row in eligible:
        signature = tuple(sorted(str(mid) for mid in row["unavailable_ids"]))

        if (
            current
            and current["date"] == row["date"]
            and current["end"] == row["start"]
            and current["signature"] == signature
        ):
            current["end"] = row["end"]
        else:
            if current:
                blocks.append(current)
            current = {
                "date": row["date"],
                "date_label": row["date_label"],
                "start": row["start"],
                "end": row["end"],
                "unavailable_names": list(row["unavailable_names"]),
                "signature": signature,
            }

    if current:
        blocks.append(current)

    for block in blocks:
        minutes = int((block["end"] - block["start"]).total_seconds() // 60)
        block["minutes"] = minutes
        block["label"] = f"{block['start'].strftime('%H:%M')}~{block['end'].strftime('%H:%M')}"

    return blocks


def get_missing_submitters(
    team: Team,
    members: Sequence[Member],
    schedules: Dict[str, PersonalSchedule],
) -> List[str]:
    member_by_id = {m.id: m for m in members}
    return [
        f"{member_by_id[mid].name}({team.member_roles.get(mid, '') or member_by_id[mid].parts[0]})"
        for mid in team.member_ids
        if mid in member_by_id and mid not in schedules
    ]


def overlap(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return max(a_start, b_start) < min(a_end, b_end)


# =========================
# UI helpers
# =========================
def check_setup() -> bool:
    if get_gas_url():
        return True
    st.error("Google Apps Script URL이 설정되지 않았습니다.")
    st.code('GAS_WEB_APP_URL = "https://script.google.com/macros/s/.../exec"')
    return False


def require_admin() -> bool:
    expected = get_admin_pin()
    if not expected:
        st.warning("ADMIN_PIN이 설정되지 않아 현재 임원진 메뉴가 잠금 없이 열려 있습니다.")
        return True

    entered = st.text_input("임원진 PIN", type="password")
    if not entered:
        return False
    if entered != expected:
        st.error("PIN이 올바르지 않습니다.")
        return False
    return True


def show_period_summary(period: SchedulePeriod) -> None:
    st.caption(
        f"{period.name} · "
        f"{period.start_date.strftime('%Y.%m.%d')} ~ {period.end_date.strftime('%Y.%m.%d')} · "
        f"{period.start_time.strftime('%H:%M')}~{period.end_time.strftime('%H:%M')} · "
        f"{period.slot_minutes}분 단위"
    )


def render_schedule_picker(
    period: SchedulePeriod,
    widget_key: str,
    existing: Set[str],
    mode: str,
) -> List[str]:
    date_labels, time_labels, slot_map = build_slot_maps(period)
    selected: List[str] = []

    if mode == "모바일 친화형":
        for date_label in date_labels:
            day_times = [t for t in time_labels if (date_label, t) in slot_map]
            defaults = [t for t in day_times if slot_map[(date_label, t)] in existing]
            chosen = st.multiselect(
                date_label,
                options=day_times,
                default=defaults,
                key=f"mobile_{period.id}_{widget_key}_{date_label}",
            )
            selected.extend(slot_map[(date_label, t)] for t in chosen)

    else:
        chunks = [date_labels[i:i + 5] for i in range(0, len(date_labels), 5)]
        tabs = st.tabs([
            chunk[0] if len(chunk) == 1 else f"{i + 1}주차: {chunk[0]} ~ {chunk[-1]}"
            for i, chunk in enumerate(chunks)
        ])

        for tab, chunk in zip(tabs, chunks):
            with tab:
                headers = st.columns([1.4] + [1] * len(chunk))
                headers[0].markdown("**시간**")
                for i, date_label in enumerate(chunk, start=1):
                    headers[i].markdown(f"**{date_label}**")

                for time_label in time_labels:
                    cols = st.columns([1.4] + [1] * len(chunk))
                    cols[0].markdown(f"**{time_label}**")
                    for i, date_label in enumerate(chunk, start=1):
                        slot_id = slot_map.get((date_label, time_label))
                        if not slot_id:
                            cols[i].write("-")
                            continue

                        checked = cols[i].checkbox(
                            "가능",
                            value=slot_id in existing,
                            key=f"grid_{period.id}_{widget_key}_{slot_id}",
                            label_visibility="collapsed",
                        )
                        if checked:
                            selected.append(slot_id)

    return selected

def duration_text(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes}분"
    if minutes % 60 == 0:
        return f"{minutes // 60}시간"
    return f"{minutes // 60}시간 {minutes % 60}분"


def render_date_blocks(blocks: Sequence[Dict[str, object]]) -> None:
    if not blocks:
        st.info("조건에 맞는 연속 가능 시간이 없습니다.")
        return

    by_date: Dict[str, List[Dict[str, object]]] = {}
    for block in blocks:
        by_date.setdefault(str(block["date_label"]), []).append(block)

    for date_label, day_blocks in by_date.items():
        st.markdown(f"#### {date_label}")
        for block in day_blocks:
            duration = duration_text(int(block["minutes"]))
            unavailable = list(block["unavailable_names"])
            if unavailable:
                st.warning(
                    f"{block['label']} · {duration} · 불가능: {', '.join(unavailable)}"
                )
            else:
                st.success(f"{block['label']} · {duration} · 전원 가능")


# =========================
# Pages
# =========================
def page_my_schedule(period: SchedulePeriod) -> None:
    st.header("🙋 내 스케줄 입력")
    show_period_summary(period)
    st.caption(
        "이름을 먼저 확인한 뒤 파트와 가능한 시간을 입력해 주세요. "
        "가능 시간은 날짜별로 모두 고른 뒤 마지막 저장 버튼을 눌렀을 때 한 번에 저장됩니다."
    )

    # 이름 조회는 확인 버튼을 눌렀을 때만 실행한다.
    with st.form("member_lookup_form", clear_on_submit=False):
        typed_name = st.text_input(
            "이름(풀네임)",
            value=st.session_state.get("confirmed_member_name", ""),
            placeholder="예: 조서영",
        )
        lookup_clicked = st.form_submit_button("내 정보 불러오기", type="primary")

    if lookup_clicked:
        display_name = normalize_display_name(typed_name)
        if not display_name:
            st.error("본인 풀네임을 입력해 주세요.")
            st.session_state.pop("confirmed_member_name", None)
            st.session_state.pop("confirmed_member_id", None)
            return

        try:
            members = list_members(active_only=True)
        except Exception as exc:
            st.error(f"부원 정보를 불러오지 못했습니다: {exc}")
            return

        existing_member = next(
            (m for m in members if normalize_name_key(m.name) == normalize_name_key(display_name)),
            None,
        )

        st.session_state["confirmed_member_name"] = display_name
        st.session_state["confirmed_member_id"] = existing_member.id if existing_member else ""

    display_name = st.session_state.get("confirmed_member_name", "")
    if not display_name:
        st.info("이름을 입력하고 **내 정보 불러오기**를 눌러 주세요.")
        return

    member_id = st.session_state.get("confirmed_member_id", "")

    try:
        members = list_members(active_only=True)
    except Exception as exc:
        st.error(f"부원 정보를 불러오지 못했습니다: {exc}")
        return

    existing_member = next((m for m in members if m.id == member_id), None) if member_id else None

    if existing_member:
        st.success(f"기존 부원 정보를 찾았습니다: {existing_member.name}")
        part_options = list(dict.fromkeys(PARTS + existing_member.parts))
        default_parts = existing_member.parts
        part_key = f"parts_existing_{period.id}_{existing_member.id}"
        widget_key = existing_member.id

        try:
            old_schedule = get_personal_schedule(period.id, existing_member.id)
        except Exception as exc:
            st.error(f"기존 스케줄을 불러오지 못했습니다: {exc}")
            return

        existing_slots = set(old_schedule.selected_slots) if old_schedule else set()
        if old_schedule:
            st.caption("기존에 제출한 스케줄을 불러왔습니다. 수정 후 다시 저장할 수 있습니다.")
    else:
        st.info("처음 등록하는 이름입니다. 저장하면 부원 명단에 자동으로 추가됩니다.")
        part_options = PARTS
        default_parts = []
        name_key = normalize_name_key(display_name)
        part_key = f"parts_new_{period.id}_{name_key}"
        widget_key = f"new_{name_key}"
        existing_slots = set()

    # 보기 방식 변경은 화면 구조가 달라지므로 이 선택에서만 한 번 재실행된다.
    # 실제 파트/가능시간 입력은 아래 form 안에 있어 날짜를 바꿔 입력해도 재실행되지 않는다.
    view_mode = st.radio(
        "보기 방식",
        ["모바일 친화형", "PC 격자형"],
        horizontal=True,
        key=f"view_{period.id}_{widget_key}",
    )

    with st.form(
        f"schedule_entry_form_{period.id}_{widget_key}",
        clear_on_submit=False,
    ):
        selected_parts = st.multiselect(
            "가능한 파트 (여러 개 선택 가능)",
            options=part_options,
            default=default_parts,
            key=part_key,
            placeholder="본인이 맡을 수 있는 파트를 모두 선택하세요",
        )

        st.markdown("### 가능한 시간")
        st.caption(
            "날짜별 시간을 전부 선택해도 중간에는 서버로 저장되지 않습니다. "
            "맨 아래 **내 스케줄 저장**을 눌렀을 때 한 번에 저장됩니다."
        )

        selected_slots = render_schedule_picker(
            period,
            widget_key,
            existing_slots,
            view_mode,
        )

        save_clicked = st.form_submit_button("내 스케줄 저장", type="primary")

    if save_clicked:
        if not selected_parts:
            st.error("파트를 한 개 이상 선택해 주세요.")
            return

        try:
            saved = save_member_and_schedule(
                period.id,
                display_name,
                selected_parts,
                selected_slots,
            )
        except Exception as exc:
            st.error(f"저장 중 오류가 발생했습니다: {exc}")
            return

        st.session_state["confirmed_member_name"] = saved.name
        st.session_state["confirmed_member_id"] = saved.id
        st.success(
            f"저장되었습니다! {saved.name} · "
            f"파트: {', '.join(saved.parts)} · 가능 시간 {len(selected_slots)}개"
        )

def page_team_schedule(period: SchedulePeriod) -> None:
    st.header("🎸 팀별 스케줄")
    show_period_summary(period)

    try:
        teams = list_teams(period.id)
    except Exception as exc:
        st.error(f"팀 목록을 불러오지 못했습니다: {exc}")
        return

    if not teams:
        st.info("아직 생성된 팀이 없습니다.")
        return

    labels = {
        f"{t.name}{' · ' + t.songs if t.songs else ''} · {t.id[:6]}": t
        for t in teams
    }
    selected_label = st.selectbox("팀 선택", list(labels.keys()))
    selected_team = labels[selected_label]

    try:
        team, members, schedules = get_team_bundle(selected_team.id)
    except Exception as exc:
        st.error(f"팀 데이터를 불러오지 못했습니다: {exc}")
        return

    if not team:
        st.error("팀 정보를 찾을 수 없습니다.")
        return

    member_by_id = {m.id: m for m in members}
    roster = []
    for member_id in team.member_ids:
        member = member_by_id.get(member_id)
        if member:
            role = team.member_roles.get(member_id, "") or member.parts[0]
            roster.append(f"{member.name}({role})")

    st.write("**팀원:** " + ", ".join(roster))

    missing = get_missing_submitters(team, members, schedules)
    if missing:
        st.warning("개인 스케줄 미제출: " + ", ".join(missing))
    else:
        st.success("모든 팀원이 개인 스케줄을 제출했습니다.")

    rows = analyze_team_slots(period, team, members, schedules)
    max_absent = st.radio(
        "날짜별 가능시간 표시 기준",
        [0, 1],
        format_func=lambda n: "전원 가능만 보기" if n == 0 else "1명 불참까지 포함",
        horizontal=True,
    )

    st.markdown("### 날짜별 연속 가능시간")
    render_date_blocks(group_rehearsal_blocks(rows, max_absent))

    st.markdown("### 전체 시간표")
    table_rows = [
        {
            "날짜": row["date_label"],
            "시간": row["time_label"],
            "가능 인원": f"{row['available_count']}/{row['total_count']}",
            "불가능": ", ".join(row["unavailable_names"]) or "-",
        }
        for row in rows
    ]
    st.dataframe(table_rows, use_container_width=True, hide_index=True)


def page_admin(period: Optional[SchedulePeriod]) -> None:
    st.header("⚙️ 임원진 관리")
    if not require_admin():
        return

    tab_period, tab_teams, tab_final = st.tabs([
        "일정 기간",
        "팀 관리",
        "합주 확정",
    ])

    with tab_period:
        st.subheader("일정 기간 설정")
        st.caption("새 일정 기간을 저장하면 이전 기간의 개인 스케줄과 섞이지 않습니다.")

        with st.form("period_form"):
            name = st.text_input(
                "일정 이름",
                value=period.name if period else "",
                placeholder="예: 2026 가을 정기공연",
            )

            st.caption(
                "날짜는 YYYY-MM-DD, 시간은 HH:MM 형식으로 입력해 주세요. "
                "입력 중에는 저장되지 않고 아래 저장 버튼을 눌렀을 때 한 번에 반영됩니다."
            )

            col1, col2 = st.columns(2)

            with col1:
                start_date_text = st.text_input(
                    "시작 날짜",
                    value=(
                        period.start_date.isoformat()
                        if period
                        else date.today().isoformat()
                    ),
                    placeholder="예: 2026-09-01",
                )
                start_time_text = st.text_input(
                    "하루 시작 시간",
                    value=(
                        period.start_time.strftime("%H:%M")
                        if period
                        else "18:00"
                    ),
                    placeholder="예: 18:00",
                )

            with col2:
                end_date_text = st.text_input(
                    "종료 날짜",
                    value=(
                        period.end_date.isoformat()
                        if period
                        else (date.today() + timedelta(days=14)).isoformat()
                    ),
                    placeholder="예: 2026-09-14",
                )
                end_time_text = st.text_input(
                    "하루 종료 시간",
                    value=(
                        period.end_time.strftime("%H:%M")
                        if period
                        else "22:00"
                    ),
                    placeholder="예: 22:00",
                )

            slot_minutes = st.selectbox(
                "시간 단위",
                [30, 60],
                index=0 if period and period.slot_minutes == 30 else 1,
            )
            save_clicked = st.form_submit_button(
                "일정 기간 저장",
                type="primary",
            )

        if save_clicked:
            if not name.strip():
                st.error("일정 이름을 입력해 주세요.")
            else:
                try:
                    start_date = datetime.strptime(
                        start_date_text.strip(),
                        "%Y-%m-%d",
                    ).date()
                    end_date = datetime.strptime(
                        end_date_text.strip(),
                        "%Y-%m-%d",
                    ).date()
                    start_time = datetime.strptime(
                        start_time_text.strip(),
                        "%H:%M",
                    ).time()
                    end_time = datetime.strptime(
                        end_time_text.strip(),
                        "%H:%M",
                    ).time()
                except ValueError:
                    st.error(
                        "날짜/시간 형식을 확인해 주세요. "
                        "날짜는 2026-09-01, 시간은 18:00처럼 입력하면 됩니다."
                    )
                else:
                    if end_date < start_date:
                        st.error("종료 날짜가 시작 날짜보다 빠릅니다.")
                    elif end_time <= start_time:
                        st.error("종료 시간은 시작 시간보다 늦어야 합니다.")
                    else:
                        try:
                            save_period(
                                name,
                                start_date,
                                end_date,
                                start_time,
                                end_time,
                                slot_minutes,
                            )
                            st.success("일정 기간이 저장되었습니다.")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"저장 중 오류: {exc}")

    with tab_teams:
        if not period:
            st.info("먼저 일정 기간을 설정해 주세요.")
        else:
            st.subheader("팀 생성")
            st.caption(
                "부원들이 '내 스케줄 입력'에서 등록하면 자동으로 나타납니다. "
                "여러 파트가 가능한 부원은 이 팀에서 맡을 파트 하나만 선택합니다."
            )

            try:
                members = list_members(active_only=True)
            except Exception as exc:
                st.error(f"부원 목록을 불러오지 못했습니다: {exc}")
                members = []

            if not members:
                st.info("아직 등록된 부원이 없습니다. 부원들이 먼저 개인 스케줄을 저장해야 합니다.")
            else:
                label_to_member = {
                    f"{m.name} · {', '.join(m.parts)}": m
                    for m in members
                }

                song_name = st.text_input(
                    "곡명",
                    placeholder="예: Ditto",
                    key="team_song_name",
                    help="입력한 곡명이 그대로 팀 이름으로 사용됩니다.",
                )
                selected_labels = st.multiselect(
                    "팀원 선택",
                    options=list(label_to_member.keys()),
                    key="team_member_select",
                )
                selected_members = [label_to_member[label] for label in selected_labels]

                member_roles: Dict[str, str] = {}
                if selected_members:
                    st.markdown("#### 이 팀에서 맡을 파트")
                    for member in selected_members:
                        if len(member.parts) == 1:
                            role = member.parts[0]
                            st.write(f"**{member.name}** → {role}")
                        else:
                            role = st.selectbox(
                                member.name,
                                options=member.parts,
                                key=f"team_role_{member.id}",
                                help=f"등록된 파트: {', '.join(member.parts)}",
                            )
                        member_roles[member.id] = role

                if st.button("팀 생성", type="primary", key="create_team_button"):
                    if not song_name.strip():
                        st.error("곡명을 입력해 주세요.")
                    elif not member_roles:
                        st.error("팀원을 한 명 이상 선택해 주세요.")
                    else:
                        try:
                            # 곡명 자체를 팀 이름으로 사용한다. songs 컬럼은 새 팀에서는 비워 둔다.
                            create_team(period.id, song_name.strip(), "", member_roles)
                            st.success("팀이 생성되었습니다.")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"팀 생성 중 오류: {exc}")

            st.subheader("현재 팀")
            try:
                teams = list_teams(period.id)
            except Exception as exc:
                st.error(f"팀 목록 오류: {exc}")
                teams = []

            for team_item in teams:
                with st.container(border=True):
                    col_a, col_b = st.columns([5, 1])
                    # 새 팀은 곡명 자체가 팀 이름이다. 예전 팀 데이터의 songs 값은 호환을 위해서만 표시한다.
                    title = team_item.name + (f" · {team_item.songs}" if team_item.songs else "")
                    col_a.markdown(f"**{title}**")

                    try:
                        bundle_team, bundle_members, _ = get_team_bundle(team_item.id)
                        if bundle_team:
                            member_by_id = {m.id: m for m in bundle_members}
                            roster = []
                            for member_id in bundle_team.member_ids:
                                member = member_by_id.get(member_id)
                                if member:
                                    role = bundle_team.member_roles.get(member_id, "") or member.parts[0]
                                    roster.append(f"{member.name}({role})")
                            col_a.caption(" · ".join(roster))
                    except Exception:
                        pass

                    if col_b.button("삭제", key=f"delete_team_{team_item.id}"):
                        try:
                            delete_team(team_item.id)
                            st.rerun()
                        except Exception as exc:
                            st.error(f"삭제 오류: {exc}")

    with tab_final:
        if not period:
            st.info("먼저 일정 기간을 설정해 주세요.")
            return

        st.subheader("합주시간 확정")

        try:
            teams = list_teams(period.id)
        except Exception as exc:
            st.error(f"팀 목록 오류: {exc}")
            teams = []

        if teams:
            team_labels = {f"{t.name} · {t.id[:6]}": t for t in teams}
            selected_label = st.selectbox("팀", list(team_labels.keys()), key="final_team")
            selected_team = team_labels[selected_label]

            try:
                team, members, schedules = get_team_bundle(selected_team.id)
            except Exception as exc:
                st.error(f"팀 데이터 오류: {exc}")
                team, members, schedules = None, [], {}

            if team:
                rows = analyze_team_slots(period, team, members, schedules)
                blocks = group_rehearsal_blocks(rows, max_absent=0)

                if blocks:
                    block_labels = {
                        f"{b['date_label']} {b['label']} ({duration_text(int(b['minutes']))})": b
                        for b in blocks
                    }
                    chosen_label = st.selectbox(
                        "전원 가능한 연속 시간",
                        list(block_labels.keys()),
                    )
                    chosen = block_labels[chosen_label]

                    if st.button("이 시간으로 합주 확정", type="primary"):
                        try:
                            save_final_schedule(
                                period.id,
                                team.id,
                                chosen["start"],
                                chosen["end"],
                            )
                            st.success("합주시간을 확정했습니다.")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"확정 저장 오류: {exc}")
                else:
                    st.info("현재 전원이 가능한 연속 시간이 없습니다.")
        else:
            st.info("먼저 팀을 생성해 주세요.")

        st.subheader("확정된 전체 합주표")

        try:
            finals = list_final_schedules(period.id)
        except Exception as exc:
            st.error(f"확정 합주표를 불러오지 못했습니다: {exc}")
            return

        team_by_id = {t.id: t for t in teams}
        final_rows: List[Dict[str, object]] = []

        for final in finals:
            try:
                start_dt = datetime.fromisoformat(str(final["start_dt"]))
                end_dt = datetime.fromisoformat(str(final["end_dt"]))
            except Exception:
                continue

            team_id = str(final.get("team_id", ""))
            team_obj = team_by_id.get(team_id)
            final_rows.append({
                "final_id": str(final.get("final_id", "")),
                "날짜": f"{start_dt.strftime('%m/%d')}({weekday_kr(start_dt.date())})",
                "시간": f"{start_dt.strftime('%H:%M')}~{end_dt.strftime('%H:%M')}",
                "팀": team_obj.name if team_obj else team_id,
                "team_id": team_id,
                "start": start_dt,
                "end": end_dt,
            })

        st.dataframe(
            [{"날짜": r["날짜"], "시간": r["시간"], "팀": r["팀"]} for r in final_rows],
            use_container_width=True,
            hide_index=True,
        )

        if final_rows:
            delete_options = {
                f"{r['날짜']} {r['시간']} · {r['팀']}": r["final_id"]
                for r in final_rows
                if r["final_id"]
            }
            if delete_options:
                delete_label = st.selectbox(
                    "확정 합주 삭제",
                    list(delete_options.keys()),
                    key="delete_final_select",
                )
                if st.button("선택한 확정 합주 삭제", key="delete_final_button"):
                    try:
                        delete_final_schedule(str(delete_options[delete_label]))
                        st.rerun()
                    except Exception as exc:
                        st.error(f"삭제 오류: {exc}")

        # 같은 부원이 두 팀에 속해 있고 확정 시간이 겹치는지 확인
        conflicts: List[str] = []
        try:
            all_members = list_members(active_only=False)
            member_names = {m.id: m.name for m in all_members}
        except Exception:
            member_names = {}

        for i in range(len(final_rows)):
            for j in range(i + 1, len(final_rows)):
                a = final_rows[i]
                b = final_rows[j]
                if not overlap(a["start"], a["end"], b["start"], b["end"]):
                    continue

                team_a = team_by_id.get(str(a["team_id"]))
                team_b = team_by_id.get(str(b["team_id"]))
                if not team_a or not team_b:
                    continue

                for member_id in set(team_a.member_ids) & set(team_b.member_ids):
                    conflicts.append(
                        f"{member_names.get(member_id, member_id)}: "
                        f"{a['팀']} ↔ {b['팀']} ({a['날짜']})"
                    )

        if conflicts:
            st.error("합주시간 중복이 있습니다.\n\n" + "\n\n".join(f"• {c}" for c in conflicts))
        elif final_rows:
            st.success("현재 확정 합주 간 팀원 시간 중복이 없습니다.")


def main() -> None:
    st.set_page_config(
        page_title="밴드부 합주 일정",
        page_icon="🎸",
        layout="wide",
    )

    st.title("🎸 밴드부 합주 일정")
    st.caption("개인 스케줄을 한 번 입력하고 팀별 가능시간을 자동으로 확인합니다.")

    if not check_setup():
        return

    try:
        period = get_current_period()
    except Exception as exc:
        st.error(f"현재 일정 기간을 불러오지 못했습니다: {exc}")
        return

    menu = st.sidebar.radio(
        "메뉴",
        ["내 스케줄 입력", "팀별 스케줄", "임원진 관리"],
    )

    st.sidebar.divider()
    if period:
        st.sidebar.caption(f"현재 일정: {period.name}")
    else:
        st.sidebar.warning("현재 일정 기간이 없습니다.")

    if menu == "임원진 관리":
        page_admin(period)
        return

    if not period:
        st.info("임원진이 먼저 일정 기간을 설정해야 합니다.")
        return

    if menu == "내 스케줄 입력":
        page_my_schedule(period)
    elif menu == "팀별 스케줄":
        page_team_schedule(period)


if __name__ == "__main__":
    main()
