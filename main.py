import asyncio
import io
import logging
import os
import re
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.select import Select
from selenium.webdriver.support.ui import WebDriverWait


# ใช้ได้สำหรับเครื่อง local
# บน Replit แนะนำให้เก็บค่าทั้งหมดไว้ใน Secrets
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

ESS_USERNAME = os.getenv("ESS_USERNAME")
ESS_PASSWORD = os.getenv("ESS_PASSWORD")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

LOGIN_URL = "https://ess.sesa37.go.th/loginTeacher.php"
STUDENT_URL = (
    "https://ess.sesa37.go.th/teacher/pk1/"
    "editstudent_pk1view.php?update_id={student_id}"
)
HOME_VISIT_URL = (
    "https://ess.sesa37.go.th/teacher/pk1/print/"
    "createReportPK7.php?update_id={national_id}"
)


def parse_id_list(value: str | None) -> set[int]:
    """แปลงค่า '123,456' เป็น set ของตัวเลข"""
    if not value:
        return set()

    result = set()

    for item in value.split(","):
        item = item.strip()

        if item.isdigit():
            result.add(int(item))

    return result


# ต้องกำหนดอย่างน้อยหนึ่งรายการ ไม่เช่นนั้นจะปิดการใช้งานคำสั่งโดยอัตโนมัติ
ALLOWED_USER_IDS = parse_id_list(os.getenv("ALLOWED_USER_IDS"))
ALLOWED_CHANNEL_IDS = parse_id_list(os.getenv("ALLOWED_CHANNEL_IDS"))


if not ESS_USERNAME or not ESS_PASSWORD or not DISCORD_TOKEN:
    raise RuntimeError(
        "กรุณาตั้งค่า ESS_USERNAME, ESS_PASSWORD และ DISCORD_TOKEN "
        "ใน Environment Variables หรือ Replit Secrets"
    )


def is_authorized(ctx: commands.Context) -> bool:
    """
    อนุญาตเมื่อ:
    - user ID อยู่ใน ALLOWED_USER_IDS หรือ
    - channel ID อยู่ใน ALLOWED_CHANNEL_IDS
    """
    if not ALLOWED_USER_IDS and not ALLOWED_CHANNEL_IDS:
        return False

    return (
        ctx.author.id in ALLOWED_USER_IDS
        or ctx.channel.id in ALLOWED_CHANNEL_IDS
    )


def is_interaction_authorized(interaction: discord.Interaction) -> bool:
    """ตรวจสิทธิ์สำหรับคำสั่ง Slash ในห้องเดิม"""
    channel_id = interaction.channel.id if interaction.channel else None
    if not ALLOWED_USER_IDS and not ALLOWED_CHANNEL_IDS:
        return False

    return (
        interaction.user.id in ALLOWED_USER_IDS
        or channel_id in ALLOWED_CHANNEL_IDS
    )


def validate_student_id(student_id: str) -> bool:
    """
    ปรับจำนวนหลักได้ตามรูปแบบ update_id จริงของระบบ
    """
    return bool(re.fullmatch(r"\d{1,30}", student_id))


FIELD_LABELS = {
    "txtusername": "เลขบัตรประชาชน",
    "txtsid": "เลขบัตรประจำตัว",
    "txtprename": "คำนำหน้า",
    "txtname": "ชื่อจริง",
    "txtschool": "ชื่อโรงเรียน",
    "txtclass": "ระดับชั้นปี",
    "txtroom": "ห้องเรียน",
    "txtno": "เลขที่ในห้องเรียน",
    "txtstuno": "เลขที่",
    "txtphone": "เบอร์โทร",
    "txtsex": "เพศ",
    "txtnickname": "ชื่อเล่น",
    "txtage": "อายุ",
    "txtbdate": "วัน/เดือน/ปีเกิด",
    "txtnationality": "สัญชาติ",
    "txtrace": "เชื้อชาติ",
    "txtreligion": "ศาสนา",
    "txtblood": "กรุ๊ปเลือด",
    "txtweight": "น้ำหนัก (กิโลกรัม)",
    "txtheight": "ส่วนสูง (เซนติเมตร)",
    "txtaddno1": "บ้านเลขที่",
    "txtaddno2": "บ้านเลขที่",
    "txtaddmoo1": "หมู่ที่",
    "txtaddmoo2": "หมู่ที่",
    "txtaddhome1": "ชื่อหมู่บ้าน/ชุมชน",
    "txtaddhome2": "ชื่อหมู่บ้าน/ชุมชน",
    "txtaddtumbol1": "ตำบล",
    "txtaddtumbol2": "ตำบล",
    "txtaddampher1": "อำเภอ",
    "txtaddampher2": "อำเภอ",
    "txtaddprovince1": "จังหวัด",
    "txtaddprovince2": "จังหวัด",
    "txtaddcode": "รหัสไปรษณีย์ / รหัสพื้นที่",
    "txtaddhos": "โรงพยาบาลใกล้บ้าน / ในพื้นที่",
    "latvalue": "พิกัดละติจูดของบ้าน",
    "lonvalue": "พิกัดลองจิจูดของบ้าน",
    "zoomvalue": "ระดับการซูมแผนที่",
    "txthometoschool": "ระยะทางจากบ้านไปโรงเรียน (กม.)",
    "txtboxgotoschool": "วิธีการเดินทางมาโรงเรียน",
    "txtnamefather": "ชื่อ-นามสกุล บิดา",
    "txtagefather": "อายุบิดา (ปี)",
    "txtocufather": "อาชีพบิดา",
    "txttelfather": "เบอร์โทรศัพท์บิดา",
    "txtaddfather": "ที่อยู่ของบิดา",
    "txtnamemother": "ชื่อ-นามสกุล มารดา",
    "txtagemother": "อายุมารดา (ปี)",
    "txtocumother": "อาชีพมารดา",
    "txttelmother": "เบอร์โทรศัพท์มารดา",
    "txtboxparent": "บุคคลที่นักเรียนอาศัยอยู่ด้วย",
    "txtboxstatusparent": "สถานภาพของบิดามารดา",
    "txtwhopaid": "ผู้รับผิดชอบค่าใช้จ่ายในการเรียน",
    "txtbrother": "จำนวนพี่น้องทั้งหมด",
    "count": "จำนวนพี่น้องทั้งหมด",
    "txtsister": "จำนวนพี่สาว/น้องสาว",
    "txtcountbrotherocu": "จำนวนพี่น้องที่ประกอบอาชีพแล้ว",
    "txtcountbrotherstu": "จำนวนพี่น้องที่กำลังศึกษาอยู่",
    "txtsalaly": "รายได้เฉลี่ยของครอบครัวต่อปี",
    "txtspecial": "ความสามารถพิเศษ",
    "txthobby": "งานอดิเรกยามว่าง",
    "txtso": "กิจกรรมที่ช่วยเหลืองานที่บ้าน",
    "txtpk1034": "ชื่อเพื่อนสนิท",
    "txtpk1036": "บทบาทหรือลักษณะในกลุ่มเพื่อน",
    "txtpk1037": "ความง่ายในการเข้าสังคม/คบเพื่อน",
    "txtpk1038": "ความกระตือรือร้นในการทำงาน",
    "txtpk1039": "ปริมาณการมีส่วนร่วมในงานกลุ่ม",
    "txtpk1043": "นิสัย/จุดเด่นของนักเรียน",
    "txtpk1044": "ลักษณะการพูดคุยกับเพื่อน",
    "txtpk1046": "อาชีพที่ใฝ่ฝันในอนาคต",
    "txtpk1047": "ความตั้งใจต่ออาชีพในอนาคต",
    "txtpk1049": "บุคคลที่เป็นที่ปรึกษาเมื่อมีปัญหา",
    "txtpk1008": "คะแนนการประเมินตนเอง/พฤติกรรม",
    "txtpk1010": "งานเสริมหรือธุรกิจของครอบครัว",
    "txtpk1011": "รายได้เสริมจากการทำงานเสริม (บาท)",
    "txtpk1014": "ความคิดเห็นต่อสภาพแวดล้อมโรงเรียน",
    "txtpk1015": "ความอบอุ่นในห้องเรียน",
    "txtpk1015b": "ความสามัคคีในห้องเรียน",
    "txtpk1016": "ความอบอุ่นในครอบครัวที่บ้าน",
    "txtpk1016b": "ความสามัคคีในครอบครัว",
    "txtpk1017": "ปัญหาหรือความขัดแย้งในบ้าน",
    "txtpk1019": "ความพร้อมด้านปัจจัยสี่/ที่อยู่อาศัย",
    "txtpk1041": "สภาพแวดล้อมของชุมชนรอบบ้าน",
    "txtpk1042": "ความรู้สึกเห็นคุณค่าในตัวเอง",
    "txtpk1048": "พฤติกรรมเสี่ยงหรือสิ่งเสพติด",
    "txtpk1001": "โรคประจำตัว หรือปัญหาสุขภาพ",
    "txtpk1002": "ข้อจำกัดทางร่างกาย/ความพิการ",
    "txtpk1005": "ประวัติการติดทุน/ภาระหนี้สินนักเรียน",
    "txtpk1009": "ความต้องการความช่วยเหลือพิเศษ",
    "txtpk1012": "ระดับความเพียงพอของเงินไปโรงเรียน",
}


CATEGORY_ONE_FIELDS = [
    "txtusername",
    "txtsid",
    "txtprename",
    "txtname",
    "txtphone",
    "txtsex",
    "txtnickname",
    "txtage",
    "txtbdate",
    "txtnationality",
    "txtrace",
    "txtreligion",
    "txtblood",
    "txtweight",
    "txtheight",
]

CATEGORY_TWO_FIELDS = [
    "txtschool",
    "txtclass",
    "txtroom",
    "txtno",
    "txtstuno",
]

CATEGORY_THREE_FIELDS = [
    "txtaddno1",
    "txtaddno2",
    "txtaddmoo1",
    "txtaddmoo2",
    "txtaddhome1",
    "txtaddhome2",
    "txtaddtumbol1",
    "txtaddtumbol2",
    "txtaddampher1",
    "txtaddampher2",
    "txtaddprovince1",
    "txtaddprovince2",
    "txtaddcode",
    "txtaddhos",
    "latvalue",
    "lonvalue",
    "zoomvalue",
    "txthometoschool",
    "txtboxgotoschool",
]

CATEGORY_FOUR_FIELDS = [
    "txtnamefather",
    "txtagefather",
    "txtocufather",
    "txttelfather",
    "txtaddfather",
    "txtnamemother",
    "txtagemother",
    "txtocumother",
    "txttelmother",
    "txtboxparent",
    "txtboxstatusparent",
    "txtwhopaid",
    "txtbrother",
    "count",
    "txtsister",
    "txtcountbrotherocu",
    "txtcountbrotherstu",
    "txtsalaly",
]

CATEGORY_FIVE_FIELDS = [
    "txtspecial",
    "txthobby",
    "txtso",
    "txtpk1034",
    "txtpk1036",
    "txtpk1037",
    "txtpk1038",
    "txtpk1039",
    "txtpk1043",
    "txtpk1044",
    "txtpk1046",
    "txtpk1047",
    "txtpk1049",
]

CATEGORY_SIX_FIELDS = [
    "txtpk1008",
    "txtpk1010",
    "txtpk1011",
    "txtpk1014",
    "txtpk1015",
    "txtpk1015b",
    "txtpk1016",
    "txtpk1016b",
    "txtpk1017",
    "txtpk1019",
    "txtpk1041",
    "txtpk1042",
    "txtpk1048",
    "txtpk1001",
    "txtpk1002",
    "txtpk1005",
    "txtpk1009",
    "txtpk1012",
]

EXCLUDED_FIELDS = {"txtroom2", "txtclass2"}

CATEGORY_TITLES = {
    1: "📁 หมวดที่ 1: ข้อมูลส่วนตัวพื้นฐาน",
    2: "📚 หมวดที่ 2: ข้อมูลการศึกษา",
    3: "🏠 หมวดที่ 3: ที่อยู่อาศัยและการเดินทาง",
    4: "👨‍👩‍👧‍👦 หมวดที่ 4: ข้อมูลครอบครัวและผู้ปกครอง",
    5: "🎯 หมวดที่ 5: พฤติกรรม งานอดิเรก และความฝัน",
    6: "⚠️ หมวดที่ 6: การประเมินสภาพแวดล้อมและความเสี่ยง",
    7: "📝 ข้อมูลอื่น ๆ",
}


def translate_field_title(title: str) -> str:
    """แปลเฉพาะชื่อ field ที่ผู้ใช้ระบุ โดยคงค่าอื่นตาม ESS เดิม"""
    normalized_title = re.sub(r"[^a-z0-9]", "", title.lower())
    return FIELD_LABELS.get(normalized_title, title)


def get_field_category(title: str) -> tuple[int, int]:
    normalized_title = re.sub(r"[^a-z0-9]", "", title.lower())
    if normalized_title in CATEGORY_ONE_FIELDS:
        return 1, CATEGORY_ONE_FIELDS.index(normalized_title)
    if normalized_title in CATEGORY_TWO_FIELDS:
        return 2, CATEGORY_TWO_FIELDS.index(normalized_title)
    if normalized_title in CATEGORY_THREE_FIELDS:
        return 3, CATEGORY_THREE_FIELDS.index(normalized_title)
    if normalized_title in CATEGORY_FOUR_FIELDS:
        return 4, CATEGORY_FOUR_FIELDS.index(normalized_title)
    if normalized_title in CATEGORY_FIVE_FIELDS:
        return 5, CATEGORY_FIVE_FIELDS.index(normalized_title)
    if normalized_title in CATEGORY_SIX_FIELDS:
        return 6, CATEGORY_SIX_FIELDS.index(normalized_title)
    return 7, (
        len(CATEGORY_ONE_FIELDS)
        + len(CATEGORY_TWO_FIELDS)
        + len(CATEGORY_THREE_FIELDS)
        + len(CATEGORY_FOUR_FIELDS)
        + len(CATEGORY_FIVE_FIELDS)
        + len(CATEGORY_SIX_FIELDS)
    )


def is_masked_birth_date(value: str) -> bool:
    """ตรวจว่าวันเกิดเป็นค่าปิดบัง เช่น ../../.... หรือ **/**/****"""
    compact_value = re.sub(r"\s+", "", value)
    return bool(compact_value) and not any(
        character.isdigit() for character in compact_value
    )


def find_birth_date_value(
    driver: webdriver.Chrome,
    current_value: str,
) -> str:
    """ค้นหาวันเกิดจริงจากช่องสำรอง หากช่องที่แสดงถูกปิดบัง"""
    if not is_masked_birth_date(current_value):
        return current_value

    for input_element in driver.find_elements(By.TAG_NAME, "input"):
        field_name = (
            input_element.get_attribute("name")
            or input_element.get_attribute("id")
            or ""
        ).lower()
        if "bdate" not in field_name and "birth" not in field_name:
            continue

        candidate = input_element.get_attribute("value") or ""
        if candidate.strip() and not is_masked_birth_date(candidate):
            return candidate.strip()

        for attribute in ("data-value", "data-original", "data-date"):
            candidate = input_element.get_attribute(attribute) or ""
            if candidate.strip() and not is_masked_birth_date(candidate):
                return candidate.strip()

    return ""


def format_student_data(
    student_id: str,
    scraped_data: list[dict[str, str]],
    elapsed_seconds: float,
) -> list[str]:
    """จัดข้อมูลเป็นข้อความสำหรับ Discord โดยไม่แนบไฟล์"""
    # หน้า ESS บางครั้งมี field ซ้ำกัน จึงแสดงแต่ละคู่หัวข้อ/ข้อมูลครั้งเดียว
    unique_data: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for item in scraped_data:
        original_title = str(item.get("หัวข้อ", "รายละเอียด")).strip()
        normalized_title = re.sub(r"[^a-z0-9]", "", original_title.lower())
        if normalized_title in EXCLUDED_FIELDS:
            continue

        title = translate_field_title(original_title)
        value = str(item.get("ข้อมูล", "")).strip()
        item_key = (original_title, value)

        if not title or not value or item_key in seen:
            continue

        seen.add(item_key)
        category, field_order = get_field_category(original_title)
        if category >= 7:
            continue

        # ช่องที่ลงท้ายด้วย 1/2 เป็นข้อมูลประเภทเดียวกัน
        # เช่น txtaddtumbol1 และ txtaddtumbol2 ให้แสดงหัวข้อเดียว
        if category == 3:
            existing_item = next(
                (
                    existing
                    for existing in unique_data
                    if existing["_หมวด"] == category
                    and existing["หัวข้อ"] == title
                ),
                None,
            )
            if existing_item:
                existing_values = existing_item["ข้อมูล"].split(" / ")
                if value not in existing_values:
                    existing_item["ข้อมูล"] += f" / {value}"
                continue

        unique_data.append(
            {
                "หัวข้อ": title,
                "ข้อมูล": value,
                "_หมวด": category,
                "_ลำดับ": field_order,
            }
        )

    unique_data.sort(key=lambda item: (item["_หมวด"], item["_ลำดับ"]))

    lines = [
        f"🎓 **ข้อมูลนักเรียน**  •  เลขบัตรประชาชน `{student_id}`",
        f"📋 {len(unique_data)} รายการ  •  ⏱️ {elapsed_seconds:.1f} วินาที",
    ]

    current_category = None
    for item in unique_data:
        if item["_หมวด"] != current_category:
            current_category = item["_หมวด"]
            lines.append(f"## {CATEGORY_TITLES[current_category]}")

        title = item["หัวข้อ"]
        value = item["ข้อมูล"]
        # ป้องกันข้อมูลจากหน้าเว็บทำให้รูปแบบ Markdown ของ Discord เพี้ยน
        title = title.replace("`", "'")
        value = value.replace("`", "'")
        lines.append(f"• **{title}:** {value}")

    # Embed รองรับ description สูงสุด 4,096 ตัวอักษร
    chunks: list[str] = []
    current = ""

    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= 4000:
            current = candidate
            continue

        if current:
            chunks.append(current)
        current = line[:4000]

    if current:
        chunks.append(current)

    return chunks


def create_result_embed(
    content: str,
    title: str | None = None,
) -> discord.Embed:
    """สร้างกรอบสีเขียวสำหรับแสดงผลข้อมูลใน Discord"""
    embed = discord.Embed(
        description=content,
        color=discord.Color.green(),
    )
    if title:
        embed.title = title
    return embed


def find_national_id(scraped_data: list[dict[str, str]]) -> str | None:
    """ค้นหาเลขบัตรประชาชนสำหรับสร้างลิงก์รายงานเยี่ยมบ้าน"""
    for item in scraped_data:
        title = re.sub(
            r"[^a-z0-9]",
            "",
            str(item.get("หัวข้อ", "")).lower(),
        )
        if title != "txtusername":
            continue

        national_id = re.sub(r"\D", "", str(item.get("ข้อมูล", "")))
        if national_id:
            return national_id

    return None


class DocumentMenuSelect(discord.ui.Select):
    def __init__(self, author_id: int, national_id: str):
        self.author_id = author_id
        self.national_id = national_id
        super().__init__(
            placeholder="เลือกเอกสารที่ต้องการ",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label="ข้อมูลนักเรียน",
                    description="ดูข้อมูลที่ค้นพบด้านบน",
                    emoji="🎓",
                    value="student",
                ),
                discord.SelectOption(
                    label="รูปการเยี่ยมบ้านนักเรียน",
                    description="ดึงรายงานเยี่ยมบ้านเป็นรูปภาพ",
                    emoji="📄",
                    value="home_visit",
                ),
            ],
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "เมนูนี้ใช้ได้เฉพาะผู้ที่ค้นหาข้อมูลเท่านั้น",
                ephemeral=True,
            )
            return

        await interaction.response.edit_message(
            content=(
                "⏳ กำลังล็อกอินเข้าสู่ระบบ ESS และดึงข้อมูลนักเรียนโปรดรอสักครู่..."
            ),
            embed=None,
            view=None,
        )

        if self.values[0] == "student":
            await run_student_lookup(interaction, self.national_id)
        else:
            await run_home_visit_lookup(interaction, self.national_id)


class DocumentMenuView(discord.ui.View):
    def __init__(self, author_id: int, national_id: str):
        super().__init__(timeout=300)
        self.add_item(DocumentMenuSelect(author_id, national_id))


def create_document_menu_embed() -> discord.Embed:
    return discord.Embed(
        title="📌 เลือกเอกสารที่ต้องการ",
        description=(
            "เลือกจากเมนูด้านล่างว่าจะรับข้อมูลแบบใด"
        ),
        color=discord.Color.green(),
    )


async def run_student_lookup(
    interaction: discord.Interaction,
    student_id: str,
) -> None:
    """ดึงข้อมูลนักเรียนและส่งผลแบบ Ephemeral ในห้องเดิม"""
    started_at = time.perf_counter()

    try:
        scraped_data, student_image = await asyncio.to_thread(
            scrape_student_data,
            student_id,
        )

        if not scraped_data:
            await interaction.edit_original_response(
                content="❌ ไม่พบข้อมูลนักเรียน",
                embed=None,
            )
            return

        elapsed_seconds = time.perf_counter() - started_at
        result_chunks = format_student_data(
            student_id,
            scraped_data,
            elapsed_seconds,
        )

        if student_image:
            await interaction.delete_original_response()
            await interaction.followup.send(
                file=discord.File(
                    io.BytesIO(student_image),
                    filename=f"student_{student_id}.png",
                ),
                ephemeral=True,
            )
            await interaction.followup.send(
                embed=create_result_embed(
                    result_chunks[0],
                    title="🎓 ข้อมูลนักเรียน",
                ),
                ephemeral=True,
            )
        else:
            await interaction.edit_original_response(
                content=None,
                embed=create_result_embed(
                    result_chunks[0],
                    title="🎓 ข้อมูลนักเรียน",
                ),
            )

        for chunk in result_chunks[1:]:
            await interaction.followup.send(
                embed=create_result_embed(chunk),
                ephemeral=True,
            )

    except ESSLoginError:
        await interaction.edit_original_response(
            content="❌ เข้าสู่ระบบ ESS ไม่สำเร็จ",
            embed=None,
        )
    except ESSStudentNotFoundError:
        await interaction.edit_original_response(
            content="❌ ไม่พบข้อมูลนักเรียนหรือหน้าเว็บไม่พร้อมใช้งาน",
            embed=None,
        )
    except Exception:
        logging.exception("คำสั่ง /ess ล้มเหลว")
        await interaction.edit_original_response(
            content="ไม่สามารถดึงข้อมูลได้ กรุณาลองใหม่อีกครั้ง",
            embed=None,
        )


async def run_home_visit_lookup(
    interaction: discord.Interaction,
    national_id: str,
) -> None:
    """ดึงรายงานเยี่ยมบ้านและส่งเป็นรูปแบบ Ephemeral"""
    try:
        report_images = await asyncio.to_thread(
            scrape_home_visit_image,
            national_id,
        )

        if not report_images:
            await interaction.edit_original_response(
                content="❌ ไม่พบรายงานการเยี่ยมบ้าน",
                embed=None,
            )
            return

        await interaction.delete_original_response()
        # Discord จำกัดจำนวนไฟล์ต่อข้อความ จึงส่งทีละชุดแต่ให้ครบทุกหน้า
        for start in range(0, len(report_images), 10):
            page_files = [
                discord.File(
                    io.BytesIO(page_image),
                    filename=(
                        f"home_visit_{national_id}_page_{page_number}.png"
                    ),
                )
                for page_number, page_image in enumerate(
                    report_images[start : start + 10],
                    start=start + 1,
                )
            ]
            await interaction.followup.send(
                files=page_files,
                ephemeral=True,
            )

    except Exception:
        logging.exception("ดึงรายงานเยี่ยมบ้านไม่สำเร็จ")
        await interaction.edit_original_response(
            content="ไม่สามารถดึงรายงานการเยี่ยมบ้านได้ กรุณาลองใหม่อีกครั้ง",
            embed=None,
        )


class ESSLoginError(Exception):
    pass


class ESSStudentNotFoundError(Exception):
    pass


def create_driver() -> webdriver.Chrome:
    options = Options()

    # ใช้ headless mode ที่รองรับ Chrome รุ่นใหม่
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")

    # ไม่ใช้การหลบเลี่ยง bot detection เพราะไม่จำเป็นและไม่ควรใช้
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-notifications")

    return webdriver.Chrome(options=options)


def scrape_student_data(
    student_id: str,
) -> tuple[list[dict[str, str]], bytes | None]:
    """ล็อกอิน ESS และดึงข้อมูลพร้อมรูปนักเรียนที่แสดงบนหน้าเว็บ"""
    driver = None
    data_list: list[dict[str, str]] = []
    student_image: bytes | None = None

    try:
        driver = create_driver()
        wait = WebDriverWait(driver, 20)

        # 1. เปิดหน้าล็อกอิน
        driver.get(LOGIN_URL)

        user_input = wait.until(
            EC.visibility_of_element_located(
                (By.CSS_SELECTOR, "input[type='text']")
            )
        )

        pass_input = wait.until(
            EC.visibility_of_element_located(
                (By.CSS_SELECTOR, "input[type='password']")
            )
        )

        user_input.clear()
        user_input.send_keys(ESS_USERNAME)

        pass_input.clear()
        pass_input.send_keys(ESS_PASSWORD)

        # พยายามกดปุ่ม submit โดยตรง
        submit_buttons = driver.find_elements(
            By.CSS_SELECTOR,
            "button[type='submit'], input[type='submit']",
        )

        if submit_buttons:
            submit_buttons[0].click()
        else:
            # fallback กรณีหน้าเว็บไม่มีปุ่ม submit ที่ค้นหาได้
            pass_input.submit()

        try:
            wait.until(lambda d: d.current_url != LOGIN_URL)
        except TimeoutException as exc:
            raise ESSLoginError("เข้าสู่ระบบ ESS ไม่สำเร็จ") from exc

        # ถ้ายังอยู่หน้าล็อกอิน แสดงว่า login ไม่สำเร็จ
        if driver.current_url.startswith(LOGIN_URL):
            raise ESSLoginError("เข้าสู่ระบบ ESS ไม่สำเร็จ")

        # 2. เปิดหน้าข้อมูลนักเรียน
        student_url = STUDENT_URL.format(student_id=student_id)
        driver.get(student_url)

        try:
            wait.until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            wait.until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "input, textarea, select")
                )
            )
        except TimeoutException as exc:
            raise ESSStudentNotFoundError(
                "ไม่พบหน้าข้อมูลนักเรียน"
            ) from exc

        # 3. ดึงรูปนักเรียนจากภาพที่แสดงจริงหลังล็อกอิน
        image_candidates = driver.find_elements(By.CSS_SELECTOR, "img")
        ranked_images = []

        for image in image_candidates:
            try:
                size = image.size
                if size["width"] < 80 or size["height"] < 80:
                    continue

                description = " ".join(
                    [
                        image.get_attribute("alt") or "",
                        image.get_attribute("title") or "",
                        image.get_attribute("class") or "",
                        image.get_attribute("id") or "",
                    ]
                ).lower()
                excluded_words = (
                    "logo",
                    "banner",
                    "icon",
                    "captcha",
                    "button",
                    "header",
                )
                priority = 0 if any(
                    word in description for word in excluded_words
                ) else 1
                if any(
                    word in description
                    for word in ("student", "profile", "photo", "person")
                ):
                    priority += 2

                ranked_images.append((priority, size["width"] * size["height"], image))
            except WebDriverException:
                continue

        if ranked_images:
            ranked_images.sort(key=lambda item: (item[0], item[1]), reverse=True)
            try:
                student_image = ranked_images[0][2].screenshot_as_png
            except WebDriverException:
                logging.warning("อ่านรูปนักเรียนไม่สำเร็จ")

        # 4. ดึง input ที่ไม่ใช่ hidden
        inputs = driver.find_elements(By.TAG_NAME, "input")

        for index, inp in enumerate(inputs):
            input_type = (inp.get_attribute("type") or "text").lower()
            name = (
                inp.get_attribute("name")
                or inp.get_attribute("id")
                or f"field_{index}"
            )

            # ไม่ดึง hidden เพราะอาจเป็น token หรือค่าภายในระบบ
            if input_type in {"hidden", "submit", "button", "reset"}:
                if (
                    input_type == "hidden"
                    and "bdate" in name.lower()
                ):
                    hidden_birth_date = find_birth_date_value(
                        driver,
                        inp.get_attribute("value") or "",
                    )
                    if hidden_birth_date:
                        data_list.append(
                            {
                                "หัวข้อ": name,
                                "ข้อมูล": hidden_birth_date,
                            }
                        )
                continue

            if input_type in {"checkbox", "radio"}:
                if inp.is_selected():
                    value = inp.get_attribute("value") or "ใช่"

                    data_list.append(
                        {
                            "หัวข้อ": f"{name} (เลือก)",
                            "ข้อมูล": value.strip(),
                        }
                    )

                continue

            value = inp.get_attribute("value") or ""
            if "bdate" in name.lower() or "birth" in name.lower():
                value = find_birth_date_value(driver, value)

            if value.strip():
                data_list.append(
                    {
                        "หัวข้อ": name,
                        "ข้อมูล": value.strip(),
                    }
                )

        # 5. ดึง textarea
        for index, textarea in enumerate(
            driver.find_elements(By.TAG_NAME, "textarea")
        ):
            name = (
                textarea.get_attribute("name")
                or textarea.get_attribute("id")
                or f"รายละเอียด_{index}"
            )

            value = (
                textarea.get_attribute("value")
                or textarea.text
                or ""
            )

            if value.strip():
                data_list.append(
                    {
                        "หัวข้อ": name,
                        "ข้อมูล": value.strip(),
                    }
                )

        # 6. ดึงค่า dropdown ที่เลือกอยู่
        for index, select_element in enumerate(
            driver.find_elements(By.TAG_NAME, "select")
        ):
            name = (
                select_element.get_attribute("name")
                or select_element.get_attribute("id")
                or f"dropdown_{index}"
            )

            try:
                selected_option = Select(
                    select_element
                ).first_selected_option

                text = (
                    selected_option.text
                    or selected_option.get_attribute("value")
                    or ""
                )

                if text.strip():
                    data_list.append(
                        {
                            "หัวข้อ": name,
                            "ข้อมูล": text.strip(),
                        }
                    )

            except Exception:
                # dropdown ที่ไม่มี option ที่เลือก ไม่ควรทำให้ทั้งงานล้ม
                logging.warning("อ่าน dropdown ไม่สำเร็จ: %s", name)

        return data_list, student_image

    except (ESSLoginError, ESSStudentNotFoundError):
        raise

    except WebDriverException as exc:
        logging.exception("Selenium error")
        raise RuntimeError("ระบบเบราว์เซอร์ทำงานผิดพลาด") from exc

    except Exception as exc:
        logging.exception("Unexpected scraping error")
        raise RuntimeError("เกิดข้อผิดพลาดระหว่างดึงข้อมูล") from exc

    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                logging.warning("ปิด WebDriver ไม่สำเร็จ")


def scrape_home_visit_image(national_id: str) -> list[bytes]:
    """ดาวน์โหลด PDF รายงานเยี่ยมบ้านและแปลงทุกหน้าเป็นรูป"""
    driver = None

    try:
        driver = create_driver()
        wait = WebDriverWait(driver, 20)
        driver.get(LOGIN_URL)

        user_input = wait.until(
            EC.visibility_of_element_located(
                (By.CSS_SELECTOR, "input[type='text']")
            )
        )
        pass_input = wait.until(
            EC.visibility_of_element_located(
                (By.CSS_SELECTOR, "input[type='password']")
            )
        )
        user_input.send_keys(ESS_USERNAME)
        pass_input.send_keys(ESS_PASSWORD)

        submit_buttons = driver.find_elements(
            By.CSS_SELECTOR,
            "button[type='submit'], input[type='submit']",
        )
        if submit_buttons:
            submit_buttons[0].click()
        else:
            pass_input.submit()

        try:
            wait.until(lambda d: d.current_url != LOGIN_URL)
        except TimeoutException as exc:
            raise ESSLoginError("เข้าสู่ระบบ ESS ไม่สำเร็จ") from exc

        if driver.current_url.startswith(LOGIN_URL):
            raise ESSLoginError("เข้าสู่ระบบ ESS ไม่สำเร็จ")

        report_url = HOME_VISIT_URL.format(national_id=national_id)
        cookies = "; ".join(
            f"{cookie['name']}={cookie['value']}"
            for cookie in driver.get_cookies()
        )
        request = urllib.request.Request(
            report_url,
            headers={
                "Cookie": cookies,
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) "
                    "AppleWebKit/537.36 Chrome/120 Safari/537.36"
                ),
            },
        )

        with urllib.request.urlopen(request, timeout=30) as response:
            pdf_bytes = response.read()

        if not pdf_bytes.lstrip().startswith(b"%PDF"):
            raise RuntimeError("ระบบ ESS ไม่ได้ส่งไฟล์ PDF กลับมา")

        with tempfile.TemporaryDirectory(prefix="ess_home_visit_") as temp_dir:
            temp_path = Path(temp_dir)
            pdf_path = temp_path / "report.pdf"
            pdf_path.write_bytes(pdf_bytes)

            subprocess.run(
                [
                    "pdftoppm",
                    "-png",
                    "-r",
                    "144",
                    str(pdf_path),
                    str(temp_path / "report"),
                ],
                check=True,
                capture_output=True,
                timeout=30,
            )

            image_paths = sorted(
                temp_path.glob("report-*.png"),
                key=lambda path: int(path.stem.rsplit("-", 1)[-1]),
            )
            if not image_paths:
                raise RuntimeError("แปลง PDF เป็นรูปไม่สำเร็จ")

            return [image_path.read_bytes() for image_path in image_paths]

    except (ESSLoginError, ESSStudentNotFoundError):
        raise
    except WebDriverException as exc:
        logging.exception("อ่านรายงานเยี่ยมบ้านไม่สำเร็จ")
        raise RuntimeError("อ่านรายงานเยี่ยมบ้านไม่สำเร็จ") from exc
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                logging.warning("ปิด WebDriver รายงานเยี่ยมบ้านไม่สำเร็จ")


# Discord configuration
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None,
)


@bot.event
async def on_ready():
    logging.info("บอทพร้อมใช้งาน: %s", bot.user)
    try:
        synced_count = 0
        for guild in bot.guilds:
            bot.tree.copy_global_to(guild=guild)
            synced_commands = await bot.tree.sync(guild=guild)
            synced_count += len(synced_commands)
        logging.info("ซิงค์คำสั่ง Slash แล้ว %s คำสั่ง", synced_count)
    except Exception:
        logging.exception("ซิงค์คำสั่ง Slash ไม่สำเร็จ")


@bot.tree.command(
    name="ess",
    description="ค้นหาข้อมูลนักเรียนจากระบบ ESS",
)
@app_commands.describe(student_id="รหัส update_id ของนักเรียน")
async def ess(interaction: discord.Interaction, student_id: str):
    """เปิดเมนูเลือกเอกสารแบบส่วนตัวในห้อง Discord เดิม"""

    if not is_interaction_authorized(interaction):
        await interaction.response.send_message(
            "❌ คุณไม่มีสิทธิ์ใช้คำสั่งนี้",
            ephemeral=True,
        )
        return

    if not validate_student_id(student_id):
        await interaction.response.send_message(
            "❌ รหัสต้องประกอบด้วยตัวเลขเท่านั้น",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        embed=create_document_menu_embed(),
        view=DocumentMenuView(interaction.user.id, student_id),
        ephemeral=True,
    )


bot.run(DISCORD_TOKEN)
