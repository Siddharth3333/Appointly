import { useEffect, useState } from "react"; import { Trash2, Check, X, UserX } from "lucide-react";
import { api, fmt, toast } from "../lib/api"; import { useAuth } from "../main";
export default function Appointments() {
  const { me } = useAuth(); const [list, setList] = useState<any[] | null>(null); const [svc, setSvc] = useState<any[]>([]); const [q, setQ] = useState(""); const [st, setSt] = useState("");
  const load = () => api(`/appointments?q=${encodeURIComponent(q)}&status=${st}`).then(setList).catch((e) => toast(e.message));
  useEffect(() => { api("/services").then(setSvc); }, []); useEffect(() => { const t = setTimeout(load, 250); return () => clearTimeout(t); }, [q, st]);
  const run = async (f: () => Promise<any>, ok: string) => { try { await f(); toast(ok); load(); } catch (e: any) { toast(e.message); } };
  const create = (e: any) => { e.preventDefault(); const d: any = Object.fromEntries(new FormData(e.target)); d.start = new Date(d.start).toISOString(); d.service_id = +d.service_id;
    run(() => api("/appointments", "POST", d), "Appointment booked"); };
  const setStatus = (id: number, status: string) => { if (status === "cancelled" && !confirm("Cancel and notify the client?")) return; run(() => api(`/appointments/${id}`, "PATCH", { status }), "Updated"); };
  return <div className="space-y-4"><h1 className="text-2xl font-bold">Appointments</h1>
    <form onSubmit={create} className="card grid gap-2 md:grid-cols-6"><input name="name" className="input" placeholder="Client name" required /><input name="email" type="email" className="input" placeholder="Email" required /><input name="phone" className="input" placeholder="Phone" />
      <select name="service_id" className="input" required>{svc.filter((s) => s.active).map((s) => <option key={s.id} value={s.id}>{s.name} ({s.duration_min} min)</option>)}</select>
      <input name="start" type="datetime-local" className="input" required /><button className="btn justify-center">Book</button></form>
    <div className="flex gap-2"><input className="input max-w-xs" placeholder="Search name, email, phone" value={q} onChange={(e) => setQ(e.target.value)} />
      <select className="input max-w-[180px]" value={st} onChange={(e) => setSt(e.target.value)}><option value="">All statuses</option>{["pending", "confirmed", "completed", "cancelled", "no_show"].map((s) => <option key={s}>{s}</option>)}</select></div>
    <div className="card overflow-x-auto">{list === null ? <div className="h-24 animate-pulse rounded bg-slate-200 dark:bg-slate-700" /> : !list.length ? <p className="py-10 text-center text-slate-500">No appointments match. Add one above or share your booking link.</p> :
      <table className="w-full text-left text-sm"><thead className="text-slate-500"><tr><th className="p-2">When</th><th>Client</th><th>Service</th><th>Status</th><th /></tr></thead><tbody>{list.map((a) => <tr key={a.id} className="border-t border-slate-100 dark:border-slate-700">
        <td className="p-2">{fmt(a.start_utc, me.timezone)}</td><td>{a.client.name}<div className="text-xs text-slate-500">{a.client.email}</div></td><td>{a.service.name}</td><td><span className="badge">{a.status.replace("_", "-")}</span></td>
        <td className="space-x-1 whitespace-nowrap text-right"><button title="Complete" className="btn-ghost" onClick={() => setStatus(a.id, "completed")}><Check size={14} /></button><button title="No-show" className="btn-ghost" onClick={() => setStatus(a.id, "no_show")}><UserX size={14} /></button>
          <button title="Cancel" className="btn-ghost" onClick={() => setStatus(a.id, "cancelled")}><X size={14} /></button><button title="Delete" className="btn-ghost text-red-600" onClick={() => confirm("Delete permanently?") && run(() => api(`/appointments/${a.id}`, "DELETE", {}), "Deleted")}><Trash2 size={14} /></button></td></tr>)}</tbody></table>}</div></div>;
}
