import { useState } from "react"; import { api, toast } from "../lib/api"; import { useAuth } from "../main";
export default function Login() {
  const { reload } = useAuth(); const [reg, setReg] = useState(false); const [busy, setBusy] = useState(false);
  const submit = async (e: any) => { e.preventDefault(); setBusy(true);
    const d: any = Object.fromEntries(new FormData(e.target)); if (reg) d.timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    try { await api("/auth/" + (reg ? "register" : "login"), "POST", d); reload(); } catch (x: any) { toast(x.message); } setBusy(false); };
  return <div className="mx-auto mt-20 max-w-md px-4"><h1 className="text-3xl font-bold">Appointly</h1><p className="mb-4 text-slate-500">Bookings, clients and reminders in one place.</p>
    <form onSubmit={submit} className="card space-y-3">
      {reg && <><input name="name" className="input" placeholder="Your name" required /><input name="business_name" className="input" placeholder="Business name" required /></>}
      <input name="email" type="email" className="input" placeholder="Email" required /><input name="password" type="password" minLength={8} className="input" placeholder="Password (8+ characters)" required />
      <div className="flex gap-2"><button className="btn" disabled={busy}>{reg ? "Create account" : "Sign in"}</button><button type="button" className="btn-ghost" onClick={() => setReg(!reg)}>{reg ? "I have an account" : "Create an account"}</button></div>
    </form></div>;
}
