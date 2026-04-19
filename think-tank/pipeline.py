#!/home/admin/miniconda3/bin/python3
"""
think-tank/pipeline.py - 统一人格智库流水线
三套体系（联参/设计师/学者）共享: 采集→提取→蒸馏→入库→张力

用法:
  python pipeline.py list                          # 列出所有人物状态
  python pipeline.py list --category joint-staff    # 列出某类
  python pipeline.py extract --category joint-staff --person 毛泽东    # 提取一人
  python pipeline.py extract --category joint-staff                   # 提取全部
  python pipeline.py distill --category designers --person Goyard      # 蒸馏一人
  python pipeline.py ingest --category scholars                        # 入库全部
  python pipeline.py run --category joint-staff --all                  # 提取+蒸馏+入库全流程
  python pipeline.py run --all                                         # 三套全部跑
  python pipeline.py report --category joint-staff --topic "台海冲突"  # 生成报告
  python pipeline.py tension                                           # 张力分析（全部）
"""
import json, os, sys, time, subprocess, argparse, yaml, re, gc
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).parent
WORKSPACE = BASE.parent
THINK_TANK = BASE
CONFIG_PATH = BASE / "config.yaml"
SCRIPTS = WORKSPACE / "scripts"

# 核心脚本
EXTRACT_SCRIPT = SCRIPTS / "_extract_one.py"
DISTILL_SCRIPT = SCRIPTS / "distill_v2.py"
VECDB_SCRIPT = SCRIPTS / "incremental_vecdb.py"
TENSION_SCRIPT = BASE / "tension_framework.py"
ENGINE_SCRIPT = SCRIPTS / "distill_engine.py"
PYTHON = Path("/home/admin/miniconda3/bin/python3")

# ===== Config =====
def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)

def get_person_dir(cfg, category, person_name, group=None):
    """统一获取人物目录路径"""
    cat_cfg = cfg["categories"][category]
    root = THINK_TANK / cat_cfg["person_root"]
    if cat_cfg.get("has_groups") and group:
        return root / group / person_name
    elif cat_cfg.get("has_groups"):
        # 自动搜索 group
        for g in root.iterdir():
            if g.is_dir() and not g.name.startswith('_') and not g.name.startswith('.'):
                p = g / person_name
                if p.is_dir():
                    return p
        return None
    else:
        return root / person_name

def find_person_dir(cfg, category, person_name):
    """在 category 下搜索 person 目录（自动处理 group 层级）"""
    cat_cfg = cfg["categories"][category]
    root = THINK_TANK / cat_cfg["person_root"]
    # 直接匹配
    direct = root / person_name
    if direct.is_dir():
        return direct
    # 在 group 子目录中搜索
    for g in sorted(root.iterdir()):
        if g.is_dir() and not g.name.startswith('_') and not g.name.startswith('.'):
            p = g / person_name
            if p.is_dir():
                return p
    return None

def discover_persons(cfg, category):
    """自动发现某 category 下所有人物"""
    cat_cfg = cfg["categories"][category]
    root = THINK_TANK / cat_cfg["person_root"]
    persons = []
    for item in sorted(root.rglob("*")):
        if not item.is_dir():
            continue
        # 排除特殊目录
        if item.name in ('persona', '_distillation', '_chromadb'):
            continue
        rel = item.relative_to(root)
        # 排除 _开头的目录、文件、以及太浅的目录（category root 本身）
        if any(part.startswith('_') or part.startswith('.') for part in rel.parts):
            continue
        if len(rel.parts) < 1:
            continue
        # person 的标识：有 personality_v2.json
        has_v2 = find_v2(item) is not None
        # 或者有 materials/ 子目录（且不是空的）
        has_mats = False
        for sub in ["materials", "persona/materials"]:
            mats = item / sub
            if mats.is_dir() and any(mats.iterdir()):
                has_mats = True
                break
        if not has_v2 and not has_mats:
            continue
        # group 层级的目录本身不算 person（除非它也有 materials/）
        # 例如 joint-staff/commander/ 有 commander 下的 person，commander 本身不算
        name = item.name
        group = rel.parts[0] if len(rel.parts) >= 2 else None
        # 如果 item 是 group 目录本身（即只有一层），且它下面有子 person，不算
        if len(rel.parts) == 1:
            # 检查是否是 group（下面有子目录且有 materials/）
            children_with_mats = [c for c in item.iterdir() if c.is_dir() and not c.name.startswith('_') and (c / "materials").is_dir()]
            if children_with_mats:
                # 这是 group 目录，跳过，只认子 person
                # 但如果 group 目录自己也有 materials/，也加入
                if not has_mats:
                    continue
        persons.append({"name": name, "path": item, "group": group})
    # 去重（同名）
    seen = set()
    unique = []
    for p in persons:
        if p["name"] not in seen:
            seen.add(p["name"])
            unique.append(p)
    return unique

def get_materials(person_dir):
    """获取某人的所有素材文件"""
    for subdir in ["materials", "persona/materials"]:
        mats_dir = person_dir / subdir
        if mats_dir.is_dir() and any(mats_dir.iterdir()):
            return sorted([f for f in mats_dir.glob("*.txt") if f.stat().st_size >= 200])
    return []

def get_extraction_cache(person_dir):
    """获取已有的提取缓存"""
    for subdir in ["_extraction_cache", "persona/_extraction_cache"]:
        cache_dir = person_dir / subdir
        if cache_dir.is_dir():
            return sorted(cache_dir.glob("*.json"))
    return []

def get_person_status(person_dir, person_name):
    """获取某人的流水线状态"""
    materials = get_materials(person_dir)
    extractions = get_extraction_cache(person_dir)
    v2_path = find_v2(person_dir)
    return {
        "name": person_name,
        "materials": len(materials),
        "extractions": len(extractions),
        "personality_v2": v2_path is not None,
        "path": str(person_dir),
    }

def find_v2(person_dir):
    """查找 personality_v2.json"""
    for subdir in ["", "persona"]:
        p = person_dir / subdir / "personality_v2.json"
        if p.exists():
            return p
    return None

# ===== Phase 1: 提取 =====
def extract_person(person_dir, person_name, cat_cfg, dry_run=False):
    """提取一人: 每个素材 → _extract_one.py → extraction cache"""
    materials = get_materials(person_dir)
    if not materials:
        print(f"  ⚠️ 无素材: {person_name}")
        return 0

    cache_dir = person_dir / "_extraction_cache"
    cache_dir.mkdir(exist_ok=True)

    is_military = cat_cfg.get("extract", {}).get("is_military", 0)
    default_source = cat_cfg.get("extract", {}).get("default_source", "web")
    lacan_type = cat_cfg.get("lacan_dims", "standard")

    ok, skip, fail = 0, 0, 0
    for idx, mat in enumerate(materials):
        mid = f"{person_name}_{idx:03d}"
        out_path = cache_dir / f"{mid}.json"
        if out_path.exists():
            skip += 1
            continue
        if dry_run:
            print(f"  [WOULD] {mat.name[:50]}")
            ok += 1
            continue

        title = mat.stem
        # 判断素材来源类型
        source = default_source
        if "B站" in title or "bilibili" in title.lower():
            source = "speech"
        elif "论文" in title or "paper" in title.lower():
            source = "paper"

        cmd = [str(PYTHON), str(EXTRACT_SCRIPT), str(mat), source, str(is_military),
               str(out_path), mid, title]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            if r.returncode == 0:
                ok += 1
            else:
                fail += 1
                print(f"  ❌ {mat.name[:40]}: {r.stderr[:80]}")
        except subprocess.TimeoutExpired:
            fail += 1
            print(f"  ⏰ {mat.name[:40]}: timeout")
        except Exception as e:
            fail += 1
            print(f"  ❌ {mat.name[:40]}: {e}")
        time.sleep(0.5)  # API 间隔

    print(f"  📊 {person_name}: {ok}提取 {skip}跳过 {fail}失败 / {len(materials)}素材")
    return ok

# ===== Phase 2: 蒸馏 =====
def distill_person(person_dir, person_name, cat_cfg, dry_run=False, use_v3=False):
    """蒸馏一人: extraction cache → distill_v2.py → personality_v2.json

    Args:
        person_dir: 人物目录
        person_name: 人物名称
        cat_cfg: 分类配置
        dry_run: 是否为演练模式
        use_v3: 是否使用v3模板（含logical_tracing）
    """
    if use_v3:
        # V3模式：使用原始素材，不需要extraction cache
        v2_path = find_v2(person_dir)
        if v2_path and not dry_run:
            print(f"  ⏭️ 已有 v2: {person_name}")
            return True

        if dry_run:
            print(f"  [WOULD] v3蒸馏 → {person_name}")
            return True

        lacan_type = cat_cfg.get("lacan_dims", "standard")

        # 直接 import distill_v2 调用函数
        sys.path.insert(0, str(SCRIPTS))
        import distill_v2
        try:
            result = distill_v2.distill_person(person_dir, person_name, lacan_type=lacan_type, use_v3=True)
            print(f"  {'✅' if result else '❌'} v3蒸馏: {person_name}")
            return result
        except Exception as e:
            print(f"  ❌ v3蒸馏失败: {person_name}: {e}")
            return False
    else:
        # V2模式：使用extraction cache
        extractions = get_extraction_cache(person_dir)
        if not extractions:
            print(f"  ⚠️ 无提取缓存: {person_name}")
            return False

        v2_path = find_v2(person_dir)
        if v2_path and not dry_run:
            print(f"  ⏭️ 已有 v2: {person_name}")
            return True

        lacan_type = cat_cfg.get("lacan_dims", "standard")

        if dry_run:
            print(f"  [WOULD] 蒸馏 {len(extractions)} extractions → {person_name}")
            return True

        # 直接 import distill_v2 调用函数，不走子进程
        sys.path.insert(0, str(SCRIPTS))
        import distill_v2
        try:
            result = distill_v2.distill_person(person_dir, person_name, lacan_type=lacan_type, use_v3=False)
            print(f"  {'✅' if result else '❌'} 蒸馏: {person_name}")
            return result
        except Exception as e:
            print(f"  ❌ 蒸馏失败: {person_name}: {e}")
            return False


def ingest_person(person_dir, person_name, cat_cfg, dry_run=False):
    """入库一人: personality_v2.json → incremental_vecdb.py → ChromaDB"""
    v2_path = find_v2(person_dir)
    if not v2_path:
        print(f"  ⚠️ 无 v2: {person_name}")
        return False

    if dry_run:
        print(f"  [WOULD] 入库: {person_name}")
        return True

    # 直接 import 调用
    sys.path.insert(0, str(SCRIPTS))
    import incremental_vecdb
    try:
        result = incremental_vecdb.main_silent(person_name, person_dir)
        print(f"  {'✅' if result else '❌'} 入库: {person_name}")
        return result
    except Exception as e:
        print(f"  ❌ 入库失败: {person_name}: {e}")
        return False

# ===== Phase 4: 张力分析 =====
def run_tension(dry_run=False):
    """运行张力框架"""
    if dry_run:
        print("  [WOULD] 张力分析（全部三套）")
        return True
    cmd = [str(PYTHON), str(TENSION_SCRIPT)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode == 0:
        print(r.stdout[-500:] if len(r.stdout) > 500 else r.stdout)
        return True
    else:
        print(f"❌ 张力分析失败: {r.stderr[:300]}")
        return False

# ===== 报告生成 =====
def generate_report(cfg, category, topic, deep=False):
    """生成战略分析报告（仅联参）"""
    cat_cfg = cfg["categories"][category]
    engine = cat_cfg.get("report_engine")
    if not engine:
        print(f"  ⚠️ {category} 没有报告引擎")
        return
    engine_path = THINK_TANK / engine
    if not engine_path.exists():
        print(f"  ⚠️ 引擎不存在: {engine_path}")
        return

    cmd = [str(PYTHON), str(engine_path), topic]
    if deep:
        cmd.append("--deep")
    else:
        cmd.append("--research")
    print(f"  🚀 启动报告引擎: {topic}")
    subprocess.run(cmd)

# ===== CLI =====
def cmd_list(cfg, args):
    categories = [args.category] if args.category else list(cfg["categories"].keys())
    for cat in categories:
        cat_cfg = cfg["categories"][cat]
        display = cat_cfg["display_name"]
        persons = discover_persons(cfg, cat)
        print(f"\n{'='*60}")
        print(f"  {display} ({len(persons)}人)")
        print(f"{'='*60}")
        for p in persons:
            s = get_person_status(p["path"], p["name"])
            status = "✅" if s["personality_v2"] else "⏳"
            m = f"素材{s['materials']} → 提取{s['extractions']}"
            print(f"  {status} {p['name']:<10} {m}")

def cmd_extract(cfg, args):
    categories = [args.category] if args.category else list(cfg["categories"].keys())
    for cat in categories:
        cat_cfg = cfg["categories"][cat]
        display = cat_cfg["display_name"]
        persons = discover_persons(cfg, cat)
        if args.person:
            persons = [p for p in persons if p["name"] == args.person]
        print(f"\n📊 提取: {display} ({len(persons)}人)")
        for p in persons:
            extract_person(p["path"], p["name"], cat_cfg, dry_run=args.dry_run)

def cmd_distill(cfg, args):
    categories = [args.category] if args.category else list(cfg["categories"].keys())
    for cat in categories:
        cat_cfg = cfg["categories"][cat]
        display = cat_cfg["display_name"]
        persons = discover_persons(cfg, cat)
        if args.person:
            persons = [p for p in persons if p["name"] == args.person]
        print(f"\n🧠 蒸馏: {display} ({len(persons)}人)")
        for p in persons:
            distill_person(p["path"], p["name"], cat_cfg, dry_run=args.dry_run, use_v3=args.v3)

def cmd_ingest(cfg, args):
    categories = [args.category] if args.category else list(cfg["categories"].keys())
    for cat in categories:
        cat_cfg = cfg["categories"][cat]
        display = cat_cfg["display_name"]
        persons = discover_persons(cfg, cat)
        if args.person:
            persons = [p for p in persons if p["name"] == args.person]
        print(f"\n📦 入库: {display} ({len(persons)}人)")
        for p in persons:
            ingest_person(p["path"], p["name"], cat_cfg, dry_run=args.dry_run)

def cmd_run(cfg, args):
    categories = [args.category] if args.category else list(cfg["categories"].keys())
    for cat in categories:
        cat_cfg = cfg["categories"][cat]
        display = cat_cfg["display_name"]
        persons = discover_persons(cfg, cat)
        if args.person:
            persons = [p for p in persons if p["name"] == args.person]
        if args.all or args.person or (not args.skip_extract):
            print(f"\n{'='*60}")
            print(f"  🔄 全流程: {display} ({len(persons)}人)")
            print(f"{'='*60}")
            for p in persons:
                print(f"\n--- {p['name']} ---")
                if not args.skip_extract:
                    extract_person(p["path"], p["name"], cat_cfg)
                distill_person(p["path"], p["name"], cat_cfg, use_v3=args.v3)
                ingest_person(p["path"], p["name"], cat_cfg)

def main():
    parser = argparse.ArgumentParser(description="统一人格智库流水线",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python pipeline.py list                    # 列出所有人物状态
  python pipeline.py extract --category scholars --person 张维迎
  python pipeline.py distill --category designers
  python pipeline.py run --category joint-staff --person 毛泽东
  python pipeline.py run --all                # 全部全流程
  python pipeline.py tension                  # 张力分析
  python pipeline.py report -c joint-staff -t "台海冲突"
        """)
    sub = parser.add_subparsers(dest="command")

    # list
    p_list = sub.add_parser("list", help="列出人物状态")
    p_list.add_argument("-c", "--category")

    # extract
    p_ext = sub.add_parser("extract", help="素材提取")
    p_ext.add_argument("-c", "--category")
    p_ext.add_argument("-p", "--person")
    p_ext.add_argument("--dry-run", action="store_true")

    # distill
    p_dis = sub.add_parser("distill", help="人格蒸馏")
    p_dis.add_argument("-c", "--category")
    p_dis.add_argument("-p", "--person")
    p_dis.add_argument("--dry-run", action="store_true")
    p_dis.add_argument("--v3", action="store_true", help="使用v3模板（含logical_tracing）")

    # ingest
    p_ing = sub.add_parser("ingest", help="向量入库")
    p_ing.add_argument("-c", "--category")
    p_ing.add_argument("-p", "--person")
    p_ing.add_argument("--dry-run", action="store_true")

    # run (全流程)
    p_run = sub.add_parser("run", help="全流程 (extract+distill+ingest)")
    p_run.add_argument("-c", "--category")
    p_run.add_argument("-p", "--person")
    p_run.add_argument("--all", action="store_true")
    p_run.add_argument("--skip-extract", action="store_true")
    p_run.add_argument("--dry-run", action="store_true")
    p_run.add_argument("--v3", action="store_true", help="使用v3模板（含logical_tracing）")

    # tension
    sub.add_parser("tension", help="张力分析")

    # report
    p_rep = sub.add_parser("report", help="生成战略报告")
    p_rep.add_argument("-c", "--category", required=True)
    p_rep.add_argument("-t", "--topic", required=True)
    p_rep.add_argument("--deep", action="store_true")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    cfg = load_config()

    cmds = {
        "list": cmd_list,
        "extract": cmd_extract,
        "distill": cmd_distill,
        "ingest": cmd_ingest,
        "run": cmd_run,
    }

    if args.command == "tension":
        run_tension(args.dry_run if hasattr(args, 'dry_run') else False)
    elif args.command == "report":
        generate_report(cfg, args.category, args.topic, args.deep)
    elif args.command in cmds:
        cmds[args.command](cfg, args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
