import pytest
from backend.models import LocString


@pytest.fixture
def sample_loc_string():
    """A single LocString with Korean and English."""
    return LocString(
        key="Buff/B_Test_Name",
        type="Text",
        desc="",
        translations={
            "Korean": "테스트 버프",
            "English": "Test Buff",
            "Chinese": "测试增益",
        },
        source_file="LangDataDB.csv",
    )


@pytest.fixture
def sample_base_strings():
    """A dict of LocStrings simulating base game extraction."""
    return {
        "Buff/B_Armor_P_1_Name": LocString(
            key="Buff/B_Armor_P_1_Name",
            type="Text",
            desc="",
            translations={"Korean": "방어력 증가", "English": "Armor Increased", "Chinese": "防御力增加"},
            source_file="LangDataDB.csv",
        ),
        "Buff/B_Armor_P_1_Description": LocString(
            key="Buff/B_Armor_P_1_Description",
            type="Text",
            desc="",
            translations={"Korean": "방어력이 증가합니다.", "English": "Defense is increased.", "Chinese": "防御力增加。"},
            source_file="LangDataDB.csv",
        ),
        "Skill/S_FireBolt_Name": LocString(
            key="Skill/S_FireBolt_Name",
            type="Text",
            desc="",
            translations={"Korean": "화염구", "English": "Fire Bolt", "Chinese": "火球术"},
            source_file="LangDataDB.csv",
        ),
        "Skill/S_FireBolt_Description": LocString(
            key="Skill/S_FireBolt_Description",
            type="Text",
            desc="",
            translations={"Korean": "적에게 &a의 피해를 줍니다.", "English": "Deal &a damage to an enemy.", "Chinese": "对敌人造成&a点伤害。"},
            source_file="LangDataDB.csv",
        ),
        "Battle/Keyword/Innate": LocString(
            key="Battle/Keyword/Innate",
            type="Text",
            desc="",
            translations={"Korean": "고유", "English": "Innate", "Chinese": "固有"},
            source_file="LangSystemDB.csv",
        ),
        "Battle/Keyword/Innate_Desc": LocString(
            key="Battle/Keyword/Innate_Desc",
            type="Text",
            desc="",
            translations={"Korean": "이 스킬은 항상 손에 있습니다.", "English": "This skill is always in your hand.", "Chinese": "此技能始终在手中。"},
            source_file="LangSystemDB.csv",
        ),
        "Battle/SkillTooltip/Accuracy": LocString(
            key="Battle/SkillTooltip/Accuracy",
            type="Text",
            desc="",
            translations={"Korean": "명중률", "English": "Accuracy", "Chinese": "命中率"},
            source_file="LangSystemDB.csv",
        ),
        "System/Debuff": LocString(
            key="System/Debuff",
            type="Text",
            desc="",
            translations={"Korean": "디버프", "English": "Debuff", "Chinese": "减益"},
            source_file="LangSystemDB.csv",
        ),
        "System/StatDesc/CritChance": LocString(
            key="System/StatDesc/CritChance",
            type="Text",
            desc="",
            translations={"Korean": "치명타 확률 {0}%", "English": "Critical Hit Chance {0}%", "Chinese": "暴击率 {0}%"},
            source_file="LangSystemDB.csv",
        ),
        "Character/Lucy_Name": LocString(
            key="Character/Lucy_Name",
            type="Text",
            desc="",
            translations={"Korean": "루시", "English": "Lucy", "Chinese": "露西"},
            source_file="LangDataDB.csv",
        ),
        "SkillExtended/SE_Heal_Name": LocString(
            key="SkillExtended/SE_Heal_Name",
            type="Text",
            desc="",
            translations={"Korean": "치유", "English": "Heal", "Chinese": "治愈"},
            source_file="LangDataDB.csv",
        ),
        "Character/Lucy_PassiveName": LocString(
            key="Character/Lucy_PassiveName",
            type="Text",
            desc="",
            translations={"Korean": "루시의 축복", "English": "Lucy's Blessing", "Chinese": "露西的祝福"},
            source_file="LangDataDB.csv",
        ),
    }


@pytest.fixture
def glossary_categories():
    """Standard Chrono Ark glossary categories."""
    return {
        "characters": "Character/",
        "buffs/debuffs": "Buff/",
        "skills": ["Skill/", "SkillExtended/"],
        "items": "Item_Equip/",
        "passives": "Item_Passive/",
    }


@pytest.fixture
def tmp_storage(tmp_path):
    """Temporary storage directory mimicking backend/storage/."""
    (tmp_path / "mods" / "12345").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def sample_csv_content():
    """Raw CSV content for a standard LangDataDB file."""
    return (
        "Key,Type,Desc,Korean,English,Japanese,Chinese,Chinese-TW [zh-tw]\n"
        "Buff/B_Test_Name,Text,,테스트,Test,,测试,測試\n"
        "Buff/B_Test_Description,Text,,설명,Description,,描述,描述\n"
        "Skill/S_Slash_Name,Text,,베기,Slash,,斩击,斬擊\n"
    )


@pytest.fixture(autouse=True)
def _isolated_published_times(tmp_path, monkeypatch):
    """Keep tests from writing publish times into the real storage folder."""
    from backend.games.total_war_warhammer_3 import published_times

    monkeypatch.setattr(published_times, "_path", lambda: tmp_path / "published_times.json")
