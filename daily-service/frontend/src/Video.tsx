import {useEffect, useRef, useState} from 'react';
import {load} from '@kinescope/player-iframe-api-loader';
import type {Kinescope} from '@kinescope/player-iframe-api-loader/types';
import {post} from './api';
export interface Playback {watch_id:string;url:string;lesson_id:string}
export default function Video({watch,onProgress,onError}:{watch:Playback;onProgress:(v:{seconds:number;completed:boolean})=>void;onError:(v:string)=>void}){
  const host=useRef<HTMLDivElement>(null);const callbacks=useRef({onProgress,onError});callbacks.current={onProgress,onError};
  const [loading,setLoading]=useState(true);
  useEffect(()=>{
    let disposed=false;let player:Kinescope.IframePlayer.Player|undefined;let playing=false;let lastWall=performance.now();let lastPosition:number|null=null;let rate=1;let pending=0;let sequence=0;let flushing=false;
    const queue:{sequence:number;seconds:number}[]=[];
    const send=async()=>{if(flushing)return;flushing=true;try{while(queue.length){const data=await post('/watch/'+watch.watch_id+'/chunk',queue[0]);queue.shift();callbacks.current.onProgress(data);}}catch(err){callbacks.current.onError('Прогресс пока не отправлен. Восстановим соединение и попробуем снова. '+(err as Error).message);}finally{flushing=false;}};
    const flush=()=>{if(pending>0){queue.push({sequence:sequence++,seconds:Math.min(pending,20)});pending=0;}void send();};
    const reset=()=>{lastWall=performance.now();lastPosition=null;};
    const visible=()=>{if(document.hidden){playing=false;void player?.pause();flush();}reset();};
    const timer=window.setInterval(flush,10000);
    document.addEventListener('visibilitychange',visible);window.addEventListener('pagehide',flush);window.addEventListener('online',flush);
    const node=document.createElement('div');host.current?.appendChild(node);
    void (async()=>{try{const factory=await load();if(disposed)return;const instance=await factory.create(node,{url:watch.url,size:{width:'100%',height:'100%'},behavior:{autoPlay:true,playsInline:true,localStorage:false},ui:{language:'ru'}});if(disposed){await instance.destroy();return;}player=instance;setLoading(false);
      instance.on(instance.Events.Playing,()=>{playing=true;reset();});
      instance.on(instance.Events.Waiting,()=>{playing=false;reset();});
      instance.on(instance.Events.Pause,()=>{playing=false;flush();reset();});
      instance.on(instance.Events.Ended,()=>{playing=false;flush();reset();});
      instance.on(instance.Events.Seeked,reset);
      instance.on(instance.Events.PlaybackRateChange,e=>{rate=e.data.playbackRate;reset();});
      instance.on(instance.Events.TimeUpdate,e=>{const clock=performance.now();const elapsed=(clock-lastWall)/1000;const position=e.data.currentTime;if(playing&&!document.hidden&&lastPosition!==null){const delta=position-lastPosition;if(delta>0&&delta<=elapsed*rate*1.5+0.15&&elapsed<3)pending+=Math.min(elapsed,delta/rate);}lastPosition=position;lastWall=clock;if(pending>=10)flush();});
      instance.on(instance.Events.Error,()=>{playing=false;callbacks.current.onError('Не удалось загрузить видео. Попробуйте запустить его ещё раз.');});
    }catch{if(!disposed){setLoading(false);callbacks.current.onError('Не удалось подключить видеоплеер. Проверьте соединение.');}}})();
    return()=>{disposed=true;flush();clearInterval(timer);document.removeEventListener('visibilitychange',visible);window.removeEventListener('pagehide',flush);window.removeEventListener('online',flush);if(player)void player.destroy();else node.remove();};
  },[watch.watch_id]);
  return <div className="video-embed" ref={host}>{loading&&<span className="video-loading spinner-border" role="status" aria-label="Загрузка видео"/>}</div>;
}
