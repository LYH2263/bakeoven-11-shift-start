import { useEffect, useState } from "react";
import { api } from "../api/client";
type P = { id: number; name: string }; type O = { id: number; label: string };
type B = { id: number; code: string; product_name?: string; oven_label?: string; start_min: number; ferment_end?: number; bake_end?: number; status: string };
function fmt(m: number) { const h = Math.floor(m/60), mm = m%60; return `${String(h).padStart(2,"0")}:${String(mm).padStart(2,"0")}`; }
export default function BatchesPage() {
  const [products, setProducts] = useState<P[]>([]);
  const [ovens, setOvens] = useState<O[]>([]);
  const [rows, setRows] = useState<B[]>([]);
  const [pid, setPid] = useState<number | "">(""); const [oid, setOid] = useState<number | "">("");
  const [start, setStart] = useState(11 * 60); const [msg, setMsg] = useState(""); const [err, setErr] = useState("");
  const [editId, setEditId] = useState<number | null>(null);
  const [editStart, setEditStart] = useState(0);
  const [saving, setSaving] = useState(false);
  // 每次进批次页都重新拉取：改完离开再进来看到的是改后的开工分钟。
  const reload = () => api<B[]>("/batches").then(setRows);
  useEffect(() => {
    api<P[]>("/products").then(p => { setProducts(p); if (p[0]) setPid(p[0].id); });
    api<O[]>("/ovens").then(o => { setOvens(o); if (o[0]) setOid(o[0].id); });
    reload();
  }, []);
  async function create() {
    setMsg(""); setErr("");
    try {
      const b = await api<B>("/batches", { method: "POST", body: JSON.stringify({ product_id: pid, oven_id: oid, start_min: start }) });
      setMsg(`已排产 ${b.code}`);
      reload();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  }
  function beginEdit(b: B) {
    setMsg(""); setErr("");
    setEditId(b.id);
    setEditStart(b.start_min);
  }
  function cancelEdit() { setEditId(null); setErr(""); }
  async function saveStart(b: B) {
    setMsg(""); setErr("");
    setSaving(true);
    try {
      const updated = await api<B>(`/batches/${b.id}/start`, {
        method: "PATCH", body: JSON.stringify({ start_min: editStart }),
      });
      // 成功：两段一起搬家，以服务端返回为准刷新列表（甘特页进入时同样重新拉取）。
      setEditId(null);
      setMsg(`${b.code} 开工已改到 ${fmt(updated.start_min)}`);
      reload();
    } catch (e) {
      // 拒绝：开工分钟停在改前的数，甘特色块也仍在原分钟（服务端未改动）。
      setErr(e instanceof Error ? e.message : String(e));
      setEditStart(b.start_min);
    } finally { setSaving(false); }
  }
  return (<>
    <h2>批次</h2>
    <div className="toolbar">
      <select value={pid} onChange={e => setPid(Number(e.target.value))}>{products.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
      <select value={oid} onChange={e => setOid(Number(e.target.value))}>{ovens.map(o => <option key={o.id} value={o.id}>{o.label}</option>)}</select>
      <label>开工分钟 <input type="number" value={start} onChange={e => setStart(Number(e.target.value))} style={{ width: 90 }} /></label>
      <button onClick={create}>创建生产批次</button>
    </div>
    {msg && <div className="ok">{msg}</div>}
    {err && <div className="err">{err}</div>}
    <table className="table"><thead><tr><th>批次</th><th>产品</th><th>炉位</th><th>发酵</th><th>烘烤结束</th><th>开工分钟</th><th>状态</th></tr></thead>
    <tbody>{rows.map(b => <tr key={b.id}><td className="mono">{b.code}</td><td>{b.product_name}</td><td>{b.oven_label}</td>
      <td className="mono">{fmt(b.start_min)}–{fmt(b.ferment_end ?? b.start_min)}</td>
      <td className="mono">{fmt(b.bake_end ?? b.start_min)}</td>
      <td className="mono">{editId === b.id ? (
        <span className="inline-edit">
          <input type="number" value={editStart} onChange={e => setEditStart(Number(e.target.value))} style={{ width: 90 }} disabled={saving} />
          <button disabled={saving} onClick={() => saveStart(b)}>保存</button>
          <button disabled={saving} onClick={cancelEdit}>取消</button>
        </span>
      ) : (
        <span className="inline-edit"><span>{b.start_min}</span><button onClick={() => beginEdit(b)}>改</button></span>
      )}</td>
      <td>{b.status}</td></tr>)}</tbody></table>
  </>);
}
