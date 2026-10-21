import {createContext,useContext,useEffect,useState,type ReactNode} from 'react';
import {api} from './api';
export const PRODUCT={base_price:99000,full_price:199000,reminder_hour:9};
const ProductContext=createContext(PRODUCT);
export function ConfigProvider({children}:{children:ReactNode}){const [value,setValue]=useState(PRODUCT);useEffect(()=>{api('/config').then(data=>setValue({...PRODUCT,...data})).catch(()=>{});},[]);return <ProductContext.Provider value={value}>{children}</ProductContext.Provider>}
export const useProduct=()=>useContext(ProductContext);
