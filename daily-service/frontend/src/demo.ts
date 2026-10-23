import type {Dashboard} from './types';
export function demoDashboard(): Dashboard {
  const now = new Date(); const today = new Date(now.getFullYear(),now.getMonth(),now.getDate(),12);
  const iso = (d:Date) => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
  const tips = ['Проведите следующий созвон стоя.','Поставьте экран на уровне глаз.','Встаньте и немного пройдитесь.','Дайте плечам расслабиться.','Найдите удобное время для движения.','Поставьте обе стопы на пол.'];
  return {user:{id:'demo',name:'Александр',email:'demo@example.com',role:'user',reminders:true,verified:true,timezone:Intl.DateTimeFormat().resolvedOptions().timeZone},today:iso(today),access:true,access_end:iso(new Date(today.getFullYear(),today.getMonth()+1,today.getDate()-6,12)),cycle:1,current_day:7,completed:5,streak:0,completion_seconds:180,base_price:99000,full_price:199000,selected:'demo-7',calendar:Array.from({length:21},(_,i)=>{const d=new Date(today);d.setDate(d.getDate()+i-6);return {date:iso(d),number:i+1,day:i+1,cycle:1,lesson_id:'demo-'+(i+1),title:'Мягкая разминка · День '+(i+1),tip:tips[i%tips.length],duration:360,completed:i<5,seconds:i<5?180:0,locked:i>6,full_id:'full-'+(i+1),full_unlocked:false,full_trial:i<=6};})};
}
