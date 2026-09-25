"""锁定改开工分钟的两头：半开重叠拒绝（停在改前）与端点相接成功（两段一起搬）。"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.models import Batch, ConflictLog, Oven, Product


@pytest.fixture
def ctx():
    # 独立 SQLite 内存库，override get_db；不进入 TestClient 的 lifespan（那会连 postgres）。
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    p1 = Product(name="乡村欧包", ferment_min=40, bake_min=35)
    p2 = Product(name="黄油可颂", ferment_min=25, bake_min=20)
    o1 = Oven(label="一层 1 号炉", capacity_note="盘炉")
    db.add_all([p1, p2, o1])
    db.flush()
    # BO-0900：发酵 [540,580)，烘烤 [580,615)。
    # BO-1030（可颂 25/20）原排 [630,655)+[655,675)，与 BO-0900 不重叠。
    db.add_all(
        [
            Batch(product_id=p1.id, oven_id=o1.id, code="BO-0900", start_min=540, status="scheduled"),
            Batch(product_id=p2.id, oven_id=o1.id, code="BO-1030", start_min=630, status="scheduled"),
        ]
    )
    db.commit()

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    yield {"client": client, "db": db}
    app.dependency_overrides.clear()


def _batch_by_code(ctx, code: str) -> dict:
    rows = ctx["client"].get("/api/batches").json()
    return next(b for b in rows if b["code"] == code)


def _blocks_by_code(ctx, code: str) -> list[dict]:
    blocks = ctx["client"].get("/api/gantt").json()
    return sorted(
        (b for b in blocks if b["code"] == code),
        key=lambda b: b["start_min"],
    )


def test_overlap_rejected_keeps_old_start_and_gantt(ctx):
    # 改到 580：可颂烘烤段 [605,625) 与 BO-0900 烘烤段 [580,615) 半开重叠。
    res = ctx["client"].patch("/api/batches/2/start", json={"start_min": 580})
    assert res.status_code == 409

    # 批次页开工分钟保持改前的数。
    b = _batch_by_code(ctx, "BO-1030")
    assert b["start_min"] == 630
    db_b = ctx["db"].scalar(select(Batch).where(Batch.code == "BO-1030"))
    assert db_b.start_min == 630

    # 甘特上的色块停在原分钟。
    ferment, bake = _blocks_by_code(ctx, "BO-1030")
    assert (ferment["phase"], ferment["start_min"], ferment["end_min"]) == ("ferment", 630, 655)
    assert (bake["phase"], bake["start_min"], bake["end_min"]) == ("bake", 655, 675)


def test_endpoint_touch_moves_both_phases_and_persists(ctx):
    # 改到 615：发酵 [615,640) 与 BO-0900 烘烤 [580,615) 端点刚好相接，半开区间允许。
    res = ctx["client"].patch("/api/batches/2/start", json={"start_min": 615})
    assert res.status_code == 200
    assert res.json()["start_min"] == 615

    # 批次列表的发酵止、烘烤止与甘特两条色块端点相同，两段一起搬家。
    b = _batch_by_code(ctx, "BO-1030")
    assert b["start_min"] == 615
    assert b["ferment_end"] == 640
    assert b["bake_end"] == 660
    ferment, bake = _blocks_by_code(ctx, "BO-1030")
    assert (ferment["start_min"], ferment["end_min"]) == (615, b["ferment_end"])
    assert (bake["start_min"], bake["end_min"]) == (b["ferment_end"], b["bake_end"])

    # 成功不得写成一行新的重叠拒绝。
    assert ctx["client"].get("/api/conflicts").json() == []
    assert ctx["db"].scalar(select(ConflictLog)) is None

    # 离开批次页再进来：开工分钟是改后的数（重新拉取仍是 615）。
    again = _batch_by_code(ctx, "BO-1030")
    assert again["start_min"] == 615
