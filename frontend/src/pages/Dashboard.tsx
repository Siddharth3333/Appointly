import { useEffect, useState } from "react"; import { Link } from "react-router-dom";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from "recharts";
import { api, fmt } from "../lib/api"; import { useAuth } from "../main";
export default function Dashboard() {
  const { me } = useAuth(); const [s, setS] = useState<any>(null); const [up, setUp] = useState<any[] | null>(null);
  useEffect(() => { api("/stats").then(setS); api("/appointments").then((l) => setUp(l.filter((a: any) => ["pending", "confirmed"].includes(a.status) && new Date(a.start_utc) > new Date()).reverse().slice(0, 6))); }, []);
  if (!s) return <div className="space-y-3">{[1, 2, 3].map((i) => <div key={i} className="h-16 animate-pulse rounded-xl bg-slate-200 dark:bg-slate-800" />)}</div>;
  const cards: [string, any][] = [["Total", s.total], ["Today", s.today], ["Upcoming", s.upcoming], ["Completed", s.completed], ["Cancelled", s.cancelled], ["Clients", s.clients], ["Revenue", s.revenue.toFixed(2)]];
  const bars = [["Upcoming", s.upcoming, "#0f766e"], ["Completed", s.completed, "#3b5b8c"], ["Cancelled", s.cancelled, "#94a3b8"], ["No-show", s.no_show, "#b42318"]].map(([name, v, c]) => ({ name, v, c }));
  return <div className="space-y-4"><h1 className="text-2xl font-bold">Dashboard</h1>
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-7">{cards.map(([l, n]) => <div key={l} className="card"><div className="text-2xl font-bold">{n}</div><div className="text-xs text-slate-500">{l}</div></div>)}</div>
    <div className="grid gap-4 md:grid-cols-2">
      <div className="card"><h2 className="mb-2 font-semibold">Status breakdown</h2><ResponsiveContainer width="100%" height={220}><BarChart data={bars}><XAxis dataKey="name" fontSize={12} /><YAxis allowDecimals={false} fontSize={12} /><Tooltip />
        <Bar dataKey="v" radius={[6, 6, 0, 0]}>{bars.map((b, i) => <Cell key={i} fill={b.c} />)}</Bar></BarChart></ResponsiveContainer></div>
      <div className="card"><h2 className="mb-2 font-semibold">Next appointments</h2>{up === null ? null : up.length ? up.map((a) => <div key={a.id} className="flex justify-between border-b border-slate-100 py-2 text-sm last:border-0 dark:border-slate-700"><span>{fmt(a.start_utc, me.timezone)}</span><span>{a.client.name} · {a.service.name}</span></div>)
        : <p className="py-8 text-center text-slate-500">Nothing booked yet. Share your link: <a className="text-teal-700 underline" href={`/book/${me.slug}`}>{location.origin}/book/{me.slug}</a></p>}
        <Link to="/appointments" className="mt-2 inline-block text-sm text-teal-700 underline">Manage appointments</Link></div></div></div>;
}
