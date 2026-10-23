export interface User {id:string;name:string;email:string;role:string;reminders:boolean;verified:boolean;timezone:string}
export interface Day {date:string;number:number;day:number;cycle:number;lesson_id:string|null;title:string;tip:string;duration:number;completed:boolean;seconds:number;locked:boolean;full_id:string|null;full_unlocked:boolean;full_trial:boolean}
export interface Dashboard {user:User;today:string;access:boolean;access_end:string|null;cycle:number;current_day:number;completed:number;streak:number;calendar:Day[];selected:string|null;completion_seconds:number;base_price:number;full_price:number}
export interface Lesson {id:string;cycle_number:number;day:number;kind:string;title:string;tip:string;video_id:string;duration:number;published:boolean}
