"""改开工分钟（PATCH /batches/{id}）两头锁定测例。

固定排炉画面：
- BO-0900（乡村欧包，发酵 40 / 烘烤 35）09:00 开工
  → 发酵 [540,580)，烘烤 [580,615)
- BO-1140（黄油可颂，发酵 25 / 烘烤 20）原在 11:40 开工
  → 发酵 [700,725)，烘烤 [725,745)
"""

from sqlalchemy import select

from app.models.models import ConflictLog, Oven, Product


def _setup(client, db):
    country = Product(name="乡村欧包", ferment_min=40, bake_min=35)
    croissant = Product(name="黄油可颂", ferment_min=25, bake_min=20)
    oven = Oven(label="一层 1 号炉", capacity_note="盘炉")
    db.add_all([country, croissant, oven])
    db.commit()

    r1 = client.post(
        "/batches",
        json={"product_id": country.id, "oven_id": oven.id, "start_min": 540, "code": "BO-0900"},
    )
    assert r1.status_code == 200
    r2 = client.post(
        "/batches",
        json={"product_id": croissant.id, "oven_id": oven.id, "start_min": 700, "code": "BO-1140"},
    )
    assert r2.status_code == 200
    return country.id, oven.id, r1.json()["id"], r2.json()["id"]


def _conflict_count(db) -> int:
    return len(db.scalars(select(ConflictLog)).all())


def _gantt_for(client, batch_id: int):
    blocks = client.get("/gantt").json()
    mine = sorted(
        (b for b in blocks if b["batch_id"] == batch_id),
        key=lambda b: b["start_min"],
    )
    return [(b["phase"], b["start_min"], b["end_min"]) for b in mine]


def test_overlap_with_bo0900_bake_is_rejected_and_everything_stays(client, db):
    _pid, _oid, bo0900_id, target_id = _setup(client, db)
    logs_before = _conflict_count(db)

    # 600 开工 → 候选发酵 [600,625) 与 BO-0900 烘烤 [580,615) 半开重叠。
    r = client.patch(f"/batches/{target_id}", json={"start_min": 600})
    assert r.status_code == 409
    detail = r.json()["detail"]
    # 撞上的是 BO-0900 的烘烤段（半开区间重叠）。
    assert f"#{bo0900_id}" in detail
    assert "bake" in detail

    # 开工分钟停在改前的 700。
    rows = {b["id"]: b for b in client.get("/batches").json()}
    assert rows[target_id]["start_min"] == 700

    # 甘特两条色块也停在改前的位置。
    assert _gantt_for(client, target_id) == [
        ("ferment", 700, 725),
        ("bake", 725, 745),
    ]
    # BO-0900 不受影响。
    assert _gantt_for(client, bo0900_id) == [
        ("ferment", 540, 580),
        ("bake", 580, 615),
    ]

    # 拒绝不得写成一行新的重叠日志。
    assert _conflict_count(db) == logs_before


def test_move_to_touch_bo0900_bake_end_moves_both_phases(client, db):
    _pid, _oid, _bo0900_id, target_id = _setup(client, db)
    logs_before = _conflict_count(db)

    # 615 与 BO-0900 烘烤结束端点刚好相接；半开区间 [.,615) / [615,.) 不重叠。
    r = client.patch(f"/batches/{target_id}", json={"start_min": 615})
    assert r.status_code == 200
    body = r.json()
    assert body["start_min"] == 615
    assert body["ferment_end"] == 640
    assert body["bake_end"] == 660

    # 两段一起搬家：批次列表端点与甘特色块端点相同。
    rows = {b["id"]: b for b in client.get("/batches").json()}
    row = rows[target_id]
    gantt = _gantt_for(client, target_id)
    assert gantt == [
        ("ferment", 615, 640),
        ("bake", 640, 660),
    ]
    assert row["ferment_end"] == gantt[0][2]
    assert row["bake_end"] == gantt[1][2]

    # 离开批次页再进来（重新拉取）开工分钟仍是改后的数。
    again = {b["id"]: b for b in client.get("/batches").json()}
    assert again[target_id]["start_min"] == 615

    # 成功改期不产生任何冲突日志。
    assert _conflict_count(db) == logs_before
