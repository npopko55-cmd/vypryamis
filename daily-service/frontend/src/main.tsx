import React from 'react';
import {createRoot} from 'react-dom/client';
import {BrowserRouter,HashRouter} from 'react-router-dom';
import 'bootstrap/dist/css/bootstrap.min.css';
import './styles.css';
import App from './App';
import {STATIC_PREVIEW} from './preview';
const Router=STATIC_PREVIEW?HashRouter:BrowserRouter;
createRoot(document.getElementById('root')!).render(<React.StrictMode><Router><App/></Router></React.StrictMode>);
