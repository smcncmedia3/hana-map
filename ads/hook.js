// 사이트 화면 계산 결과를 꺼내는 훅. ads/plan.py 가 페이지를 열 때 index.html 의 아래 줄 바로 뒤에 끼워 넣습니다.
// ANCHOR: let RS=null, RULE=null, ACT=new Set();
window.hanaAds=function(withPois){ if(!RULE||!RS) return null; const h=S.t.h;
  const tot=PT.map((t,k)=>S.tgt[t]?RS.tn[k]*RS.tmean[k]*RULE.m[k][h]:0), ws=tot.reduce((a,b)=>a+b,0);
  const out={ver:1, time:tstr(S.t), t:S.t, hour:h, dayType:DI.type, upth:UPTH,
    types:PT.map((t,k)=>({key:t, name:PTN[t], n:RS.tn[k], now:+RULE.m[k][h].toFixed(4), on:!!S.tgt[t],
      share:ws?+(tot[k]/ws).toFixed(4):0, day:+RULE.day[k].toFixed(4), m:RULE.m[k].map(v=>+v.toFixed(4))}))};
  if(withPois){ out.pois=[]; for(let i=0;i<NP;i++){ if(!Z.poi.open[i]) continue; out.pois.push({t:PT[Z.poi.t[i]], nm:Z.poi.nm[i], ad:Z.poi.ad[i], lat:+PLL[i][1].toFixed(6), lon:+PLL[i][0].toFixed(6)}); } }
  return out; };
