"""Erzeugt Seite 1 (Ausfuehrungsschema mit Klemmen und Kabelliste) als SVG.

Aufruf: python3 ausfuehrungsschema.py <ausgabe.svg>
Inhalte (Geraete, Klemmen, Kabelliste, Hinweise) stehen als Daten im Skript und werden
je Projekt angepasst. Hilfsfunktionen: box, t, lines, arrow, tag, dev, table.
"""
import sys
R="#c62828"; G="#2e7d32"; B="#1565c0"; O="#e08600"; K="#222"; P="#5e35b1"; GR="#555"
o=[]
def a(s): o.append(s)
def box(x,y,w,h,fill="#fff",stroke=K,sw=1.5,dash=None,rx=5):
    d=f' stroke-dasharray="{dash}"' if dash else ''
    a(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}/>')
def t(x,y,s,size=12,bold=False,fill=K,anchor="start",italic=False,rot=None):
    s=s.replace('&','&amp;').replace('<','&lt;')
    w=' font-weight="700"' if bold else ''
    i=' font-style="italic"' if italic else ''
    r=f' transform="rotate({rot} {x} {y})"' if rot is not None else ''
    a(f'<text x="{x}" y="{y}" font-size="{size}"{w}{i} fill="{fill}" text-anchor="{anchor}"{r}>{s}</text>')
def lines(x,y,ls,size=11,dy=15,**k):
    for n,s in enumerate(ls): t(x,y+n*dy,s,size,**k)
def arrow(pts,col,both=False,dash=None,sw=2):
    m={R:"r",G:"g",B:"b",O:"o",K:"k"}[col]
    d=f' stroke-dasharray="{dash}"' if dash else ''
    s=f' marker-start="url(#s{m})"' if both else ''
    p=" ".join(f"{x},{y}" for x,y in pts)
    a(f'<polyline points="{p}" fill="none" stroke="{col}" stroke-width="{sw}"{d} marker-end="url(#e{m})"{s}/>')
def tag(x,y,s,col=K,ln=None):
    if ln: t(x,y+22,ln,10,True,col,"middle")
    w=10+7*len(s)
    a(f'<rect x="{x-w/2}" y="{y-9}" width="{w}" height="18" rx="9" fill="#fff" stroke="{col}" stroke-width="1.3"/>')
    t(x,y+4,s,11,True,col,"middle")
def dev(x,y,w,h,bmk,name,ls,fill="#fff",col=K):
    box(x,y,w,h,fill,col,1.6)
    a(f'<rect x="{x}" y="{y}" width="{12+8*len(bmk)}" height="20" rx="5" fill="{col}"/>')
    t(x+6,y+15,bmk,12,True,"#fff")
    t(x+20+8*len(bmk),y+15,name,12,True)
    lines(x+8,y+36,ls)


W,H=1920,1730
a(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" font-family="Arial, Helvetica, sans-serif">')
a('<defs>')
for m,c in (("r",R),("g",G),("b",B),("o",O),("k",K)):
    a(f'<marker id="e{m}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="{c}"/></marker>')
    a(f'<marker id="s{m}" viewBox="0 0 10 10" refX="1" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M10,0 L0,5 L10,10 z" fill="{c}"/></marker>')
a('</defs>')
a(f'<rect width="{W}" height="{H}" fill="#fff"/>')
t(40,42,"Ausführungsschema: RWA-Fenster mit Sonnenschutz (außen) und Verdunkelung (innen)",22,True)
t(40,66,"D+H RZN 4404-K V2 mit Jalousiemodul AT 41 · WAREMA MSE Notstrom-Kit + USV · Klemmen nach D+H 99.823.80 (06/19), D+H AT 41 Rev. 2.2 und WAREMA 2014458 (04/2022) · Seite 1 von 3",13,fill=GR)

t(40,98,"FELD / AUSLÖSUNG",11,True,GR)
dev(40,108,270,104,"S1","D+H RT 45",["RWA-Taster, max. 8 je Linie","Klemmen: 2 Linie · 1 – · 6 Kontrolle","4 Alarm · 7 ZU · 5 Störung","letzter Taster: 10 kΩ zwischen 3 – 8"],"#fdecea",R)
dev(40,236,270,100,"BMA","Brandfallsteuerung",["potentialfreier Arbeitskontakt","Alarmwiderstand 1 kΩ in Reihe","Endwiderstand 10 kΩ parallel","(Linie überwacht)"],"#fdecea",R)
dev(40,360,270,88,"UV","Unterverteilung 230 V AC",["F1: eigener Stromkreis RWA (gekennz.)","F2 / F3: USV A4 / A6 – jeweils derselbe","Abgang wie der Sonnenschutzaktor"],"#fff7e6",O)

box(420,90,590,690,"#fafafa","#888",1.5,"8 5")
t(432,108,"SCHALTSCHRANK RWA  (D+H-Gehäuse GEH-KST / GEH-S-RWA, Relais im Beistellgehäuse)",11,True,GR)
dev(440,120,340,340,"A1","D+H RZN 4404-K V2",[],"#fff5f4",R)
lines(450,156,["230 V AC, 120 VA · Ausgang 24 V DC / 4 A · 1 Linie / 1 Gruppe"],10.5)
terms=[("Netz","L · N  (PE nur Gehäuse KS)"),("RM  1 · 2","R · –   BMA / Brandmelder"),("RT  3 … 8","T · – · K · A · Z · S   RWA-Taster"),
("LT  1 … 4","L · N– · Z · A   → Steckplatz AT 41"),("MOT  5 … 7","E · a · b   → Steckplatz AT 41"),("Ext. 1 … 7","N– · N+ · Z · + · M · D1 · D2"),
("Stiftleiste","D1 · N– · N+ · S · – · A · K · Z · D2"),("DIP S1","1 = OFF (AUF-Laufzeitbegrenzung aus)")]
for n,(k,v) in enumerate(terms):
    y=172+n*26
    box(450,y,92,21,"#fff",K,1,rx=2); t(455,y+15,k,10.5,True)
    t(549,y+15,v,10.5)
box(450,388,320,62,"#fff6e0",O,1.5)
lines(458,406,["G1  Akku 2 × 12 V / 2,2 Ah (Typ 2, VdS)","Notversorgung 72 h","Sicherungen: Netz · Ext. Meldung F 1,25 A · Akku F 3,15 A"],10.5,16,bold=False,fill="#8a5200")
dev(800,120,195,160,"A2","D+H AT 41",["Jalousiemodul, Id. 30.551.70","1 Aufm. · 2 N– · 3 zu · 4 auf","5 Überw. · 6 Mot.a · 7 Mot.b","P2 – P3: Kontakt „Flügel zu“","(max. 60 V / 2 A)","P4: Meldung vom BRV-Antrieb","Verzögerung 40 … 110 s (Poti)"],"#fff5f4",R)
dev(800,292,195,56,"K1","Relais Beschattung",["Spule: + → P2 – P3 → K1 → –","2 Schließer → In3 A3 / A5"])
dev(800,358,195,50,"K2 K3","Alarm / Störung",["Abgriff A / S gegen –  → GLT"])
dev(800,418,195,50,"K6","Zeitrelais 60 s",["Laufzeitüberwachung → GLT"])
dev(440,494,165,66,"K4","Koppelrelais",["Sonnenschutz in","Parkposition (B1)"],"#f1f8f1",G)
dev(625,494,165,66,"K5","Koppelrelais",["Verdunkelung in","Parkposition (B2)"],"#f1f8f1",G)
box(440,580,555,188,"#fff",G,1.5)
t(452,602,"Ablauf und Sicherheitskette",12,True,G)
lines(452,623,["① Alarm (RT 45 / BMA) oder Lüften AUF: AT 41 öffnet den Kontakt P2 – P3 → K1 fällt ab → In3 an beiden",
"    MSE Notstrom-Kits offen → Sonnenschutz und Verdunkelung fahren hoch.",
"② Nach der am AT 41 eingestellten Zeit (60 s) laufen die Fensterantriebe an.",
"③ Solange das Fenster offen ist, bleibt P2 – P3 offen: beide Behänge sind oben gesperrt (Fahrsperre).",
"④ Lüften AUF der GLT ist über K4 ∧ K5 geführt (Kl. 4 „auf“): Freigabe nur, wenn beide Behänge oben sind.",
"⑤ K4, K5, K6 melden Parkposition und Laufzeitüberschreitung 60 s an die GLT.",
"⑥ Im Alarmfall wertet die Zentrale die Rückmeldung nicht aus – das Fenster öffnet nach Zeitablauf.",
"    Drahtbruch W7 oder Ausfall K1 = Behänge fahren hoch (Ruhestromprinzip)."],11,17.5)
arrow([(605,560),(605,580)],G,sw=1.5); arrow([(790,527),(815,527),(815,580)],G,sw=1.5)
arrow([(780,182),(800,182)],R,sw=1.5); arrow([(897,280),(897,292)],R,sw=1.5)
arrow([(780,380),(800,380)],K,sw=1.5)

arrow([(310,160),(440,160)],R); tag(375,160,"W1",R,"≤ 200 m*")
arrow([(310,286),(440,286)],R); tag(375,286,"W2",R,"≤ 200 m*")
arrow([(310,404),(440,404)],O); tag(375,404,"W3",O,"≤ 75 m*")

def warema(y,titel,col,fill,usv,mse,mot,b,rs,motname):
    box(1180,y,690,250,fill,col,2)
    t(1192,y+19,titel,13,True)
    dev(1195,y+44,175,130,usv,"WAREMA USV",["Art.-Nr. 2013 984","Netzleitung 1,8 m an Schuko","6 Ausgänge 230 V AC","Einsteckkarte, Leitung 3 m","Akkupack RBC48 (2014 827)"],"#fff7e6",O)
    dev(1425,y+44,295,130,mse,"WAREMA MSE Notstrom-Kit REG",["Art.-Nr. 2013 983 · Ausgang 500 VA","X1: 7–9 L · 4–6 N · 1–3 PE  (von USV)","X2: Motor 1 △ · 2 ▽ · 3 N · 4 PE","      Aktor 6 △ · 7 ▽ · 8 N · 9 PE","X5: 1 0V · 2 In+ · 3 In1 · 4 In2 · 5 R · 6 In3 · 7 In+","X6: 3 In4 · Störmeldung 7 NC · 8 C · 9 NO"],"#fff",col)
    arrow([(1370,y+100),(1425,y+100)],O); tag(1397,y+78,"W5",O); tag(1397,y+134,"W6",O,"3 m")
    a(f'<circle cx="1800" cy="{y+100}" r="32" fill="#fff" stroke="{K}" stroke-width="1.6"/>')
    t(1800,y+96,mot,14,True,anchor="middle"); t(1800,y+112,"230 V~",10,anchor="middle")
    t(1800,y+148,motname,10,fill=GR,anchor="middle")
    arrow([(1720,y+100),(1768,y+100)],K); tag(1744,y+78,"W9"); t(1756,y+60,"≤ 100 m*",10,True,anchor="middle")
    dev(1195,y+186,235,54,b,"Positionsschalter „oben“",["Schließer, Typ mit WAREMA klären"],"#f1f8f1",G)
    dev(1650,y+186,205,54,rs,"Reset  X5: 5 R – 7 In+",["Schlüsseltaster 2017 192 · W8"])
    dev(1440,y+186,200,54,"W10","vom Sonnenschutzaktor",["auf X2: 6 – 9 · ≤ 100 m*"],"#fff",GR)
warema(90,"Sonnenschutz außen – WAREMA Raffstore, Antrieb 230 V AC",B,"#f3f8ff","A4","A3","M2","B1","S2","Raffstore")
warema(348,"Verdunkelung innen – WAREMA, Antrieb 230 V AC",P,"#f6f3fb","A6","A5","M3","B2","S3","Verdunkelung")
box(1180,606,690,174,"#fdf3f2",R,2)
t(1192,626,"RWA-Fenster (NRWG)",13,True)
dev(1195,640,250,128,"XD1","Abzweigdose am Fenster",["Endmodul EM 47 K (2 × 47 kΩ) am","letzten Antrieb: orange → Überw.,","schwarz → Mot.a, schwarz → Mot.b","E30 nach Brandschutzkonzept"])
dev(1480,640,375,128,"M1","D+H Fensterantrieb KA / ZA …-BRV",["24 V DC mit BRV-Meldung (für AT 41 erforderlich)","Adern: weiß → Mot.a · braun → Mot.b · gelb → Meldung (P4)","Summenstrom der Gruppe ≤ 4 A","RWA-Schnelllauf (HS): Öffnungsposition in max. 60 s","Typ nach Flügelgewicht und Hub"],"#fff",R)
arrow([(1445,704),(1480,704)],R)

# K1 -> In3
a(f'<polyline points="995,320 1040,320 1040,150 1188,150 1188,122 1560,122 1560,134" fill="none" stroke="{R}" stroke-width="2" marker-end="url(#er)"/>')
tag(1110,150,"W7",R,"≤ 200 m*"); t(1110,136,"In3 / In+ (Öffnerkreis)",10.5,fill=R,anchor="middle")
a(f'<polyline points="1040,320 1040,408 1188,408 1188,380 1560,380 1560,392" fill="none" stroke="{R}" stroke-width="2" marker-end="url(#er)"/>')
tag(1110,408,"W7",R,"≤ 200 m*"); t(1110,394,"In3 / In+ (Öffnerkreis)",10.5,fill=R,anchor="middle")
# B -> K4/K5
a(f'<polyline points="1195,303 1150,303 1150,484 522,484 522,494" fill="none" stroke="{G}" stroke-width="2" marker-end="url(#eg)"/>')
tag(1150,352,"W11",G)
a(f'<polyline points="1195,561 1060,561 1060,478 707,478 707,494" fill="none" stroke="{G}" stroke-width="2" marker-end="url(#eg)"/>')
tag(1110,561,"W11",G,"≤ 200 m*")
t(1128,356,"≤ 200 m*",10,True,G,"end")
# AT41 -> XD1
a(f'<polyline points="995,200 1020,200 1020,704 1195,704" fill="none" stroke="{R}" stroke-width="2.5" marker-end="url(#er)"/>')
tag(1100,704,"W12",R,"≤ 50 m bei 4 A"); t(1100,690,"Mot.a · Mot.b · Überw. · Meldung",10.5,fill=R,anchor="middle")
# UV -> USV
a(f'<polyline points="175,448 175,800 1166,800 1166,190 1195,190" fill="none" stroke="{O}" stroke-width="2" marker-end="url(#eo)"/>')
a(f'<polyline points="1166,448 1195,448" fill="none" stroke="{O}" stroke-width="2" marker-end="url(#eo)"/>')
tag(300,800,"W4",O); t(330,792,"230 V AC je Stromkreis F2 / F3 · ≤ 75 m*",10.5,fill=O)

box(40,846,1810,104,"#eef4fc",B,3)
t(56,872,"GLT  DDC-Unterstation (potentialfreie DI / DO)",15,True,B)
lines(56,893,["DI: RWA Alarm (K2) · RWA Störung (K3) · Sonnenschutz Parkposition (K4) · Verdunkelung Parkposition (K5) · Laufzeitüberschreitung 60 s (K6) · Störmeldung MSE A3 · Störmeldung MSE A5 (X6: 8 C – 7 NC, im Normalbetrieb geschlossen)",
"DO: Lüften AUF (AT 41 Kl. 2 N– → 4 auf, über K4 ∧ K5) · Lüften ZU (AT 41 Kl. 2 N– → 3 zu) · Fahrbefehle an den Sonnenschutzaktor (vorgeschaltetes Schaltelement an X2: 6 – 9)",
"Die GLT ist im Brandfall nicht beteiligt. Betriebsmeldung je Anlage = Störmeldekontakt geschlossen."],11.5,18)
arrow([(540,780),(540,846)],B); tag(540,814,"W14",B); t(580,818,"≤ 200 m*",10,True,B)
arrow([(700,846),(700,780)],B); tag(700,814,"W13",B); t(740,818,"≤ 200 m*",10,True,B)
a(f'<polyline points="1870,215 1888,215 1888,898 1850,898" fill="none" stroke="{B}" stroke-width="2" marker-end="url(#eb)"/>')
a(f'<polyline points="1870,473 1888,473" fill="none" stroke="{B}" stroke-width="2"/>')
tag(1888,800,"W15",B); t(1914,824,"≤ 200 m*",10,True,B,"end")

def table(x,y,cols,rows,title,rh=20):
    t(x,y-8,title,13,True)
    tw=sum(c[1] for c in cols)
    box(x,y,tw,rh*(len(rows)+1),"#fff","#888",1,rx=0)
    a(f'<rect x="{x}" y="{y}" width="{tw}" height="{rh}" fill="#e9e9e9"/>')
    cx=x
    for name,w in cols:
        t(cx+5,y+14,name,10.5,True)
        if cx>x: a(f'<line x1="{cx}" y1="{y}" x2="{cx}" y2="{y+rh*(len(rows)+1)}" stroke="#bbb"/>')
        cx+=w
    for n,r in enumerate(rows):
        yy=y+rh*(n+1)
        a(f'<line x1="{x}" y1="{yy}" x2="{x+tw}" y2="{yy}" stroke="#ccc"/>')
        cx=x
        for i,((name,w),v) in enumerate(zip(cols,r)):
            t(cx+5,yy+14,v,10.5,i in (0,4)); cx+=w
kab=[
("W1","S1 RT 45: 2 · 1 · 6 · 4 · 7 · 5","A1 RT: 3 T · 4 – · 5 K · 6 A · 7 Z · 8 S","J-Y(St)Y 4×2×0,8 mm (D+H: 6 × 0,8 Ø)","200 m*","Richtwert; D+H nennt keine Grenzlänge"),
("W2","BMA: Arbeitskontakt mit 1 kΩ, 10 kΩ parallel","A1 RM: 1 R · 2 –","J-Y(St)Y 2×2×0,8 mm, rot (D+H: 2 × 0,8 Ø)","200 m*","Richtwert; Schleife 15 Ω ≪ 1 kΩ"),
("W3","UV F1: L · N · PE","A1 Netzklemme: L · N","NYM-J 3×1,5 mm²","75 m*","rechnerisch, Abschaltbedingung B 16 A"),
("W4","UV F2 / F3: L · N · PE","Schuko-Steckdose der USV A4 / A6","NYM-J 3×1,5 mm²","75 m*","rechnerisch; USV-Netzleitung 1,8 m fest"),
("W5","USV A4 / A6: Ausgang (Kaltgerätestecker)","MSE A3 / A5 X1: 7–9 L · 4–6 N · 1–3 PE","H05RR-F 3G1,5 mm²","kurz","WAREMA ohne Angabe; gleicher Verteiler"),
("W6","USV A4 / A6: Einsteckkarte","MSE A3 / A5 X5: 2 In+ · 3 In1 · 4 In2","vorkonfektioniert; Verlängerung 2×2×0,8 geschirmt","3 m","WAREMA: werksseitige Leitungslänge"),
("W7","K1: Schließer 13–14 / 23–24","MSE A3 / A5 X5: 6 In3 · 7 In+ (Brücke entfernen)","JE-H(St)H…Bd FE180/E30 2×2×0,8 mm, geschirmt","200 m*","Richtwert; Eingang 24 V DC, 1–2 mA"),
("W8","S2 / S3 Schlüsseltaster","MSE A3 / A5 X5: 5 R · 7 In+","J-Y(St)Y 2×2×0,8 mm","200 m*","Richtwert; WAREMA: 2×2×0,8 Ø geschirmt"),
("W9","MSE A3 / A5 X2: 1 △ · 2 ▽ · 3 N · 4 PE","Verteilerdose → M2 / M3 (Motorleitung H05RR-F 4G0,75)","NHXH-J FE180/E30 4×1,5 mm² (WAREMA: 4 × 1,5 mm²)","100 m*","rechnerisch, Spannungsfall < 1,5 % bei 1,4 A"),
("W10","Sonnenschutzaktor: △ · ▽ · N · PE","MSE A3 / A5 X2: 6 △ · 7 ▽ · 8 N · 9 PE","NYM-J 4×1,5 mm²","100 m*","rechnerisch; Bemessungsstrom 2 A"),
("W11","B1 / B2: Schließer","K4 / K5: Spule A1 – A2 (über + / –)","JE-H(St)H…Bd FE180/E30 2×2×0,8 mm","200 m*","Richtwert"),
("W12","A2 AT 41: 6 Mot.a · 7 Mot.b · 5 Überw. · P4 Meldung","XD1 → M1 weiß · braun · gelb; EM 47 K","NHXH FE180/E30 5×2,5 mm², gn/ge nicht belegen","50 / 100 / 200 m","D+H-Tabelle 2,5 mm² bei 4 / 2 / 1 A  (1,5 mm²: 30 / 60 / 120 m)"),
("W13","GLT DO: Lüften AUF / ZU","A2 AT 41: 2 N– · 3 zu · 4 auf","J-Y(St)Y 2×2×0,8 mm (D+H: 4 × 0,8 Ø)","200 m*","Richtwert"),
("W14","K2 · K3 · K4 · K5 · K6: Wechsler","GLT DI","J-Y(St)Y 10×2×0,8 mm","200 m*","Richtwert"),
("W15","MSE A3 / A5 X6: 7 NC · 8 C · 9 NO","GLT DI","J-Y(St)Y 2×2×0,8 mm","200 m*","Richtwert; Kontakt max. 3 A, nicht an BMZ"),
("W16","A1 Ext. 4 „+“ → A2 P2 – P3 → K1 → A1 „–“","Schaltschrankverdrahtung K1 … K6","H05V-K 0,75 mm²","intern","nach D+H-Plan AT 41, Blatt 4/4"),]
table(40,990,[("Kabel",52),("von (Gerät: Klemme)",330),("nach (Gerät: Klemme)",340),("Typ",350),("max. Länge",118),("Grundlage",650)],kab,"Kabelliste mit Klemmen und maximaler Leitungslänge")
t(40,1352,"* Kein Herstellergrenzwert vorhanden – eigene Berechnung: Signalleitung 0,8 mm Ø = 73 Ω/km Schleife, 200 m ≈ 15 Ω. 230-V-Leitungen 1,5 mm² hinter LS B 16 A, Vorimpedanz 0,6 Ω angenommen; Nachweis durch den Elektroplaner.",10.5,fill=GR)
t(40,1368,"   W12 nach D+H-Formel: Querschnitt (mm²) = Leitungslänge (m) × Gesamtstrom (A) / 80.",10.5,fill=GR)

box(40,1386,1840,326,"#fffdf5",O,1.5)
t(56,1410,"Vor Ausführung zu klären",14,True,"#8a5200")
notes=[
"1.  Hersteller der Zentrale: DEHN fertigt Überspannungs- und Blitzschutz, keine RWA-Zentralen. Gezeichnet ist deshalb D+H Mechatronic.",
"2.  Rückmeldung: Die RZN 4404-K V2 hat keinen Eingang für „Behang oben“. D+H löst die Reihenfolge über das AT 41 rein zeitlich; die Rückmeldungen B1 / B2 wirken auf Lüftungsfreigabe und GLT, nicht auf die Alarmöffnung.",
"3.  60 Sekunden: Das AT 41 ist auf das 1,5-Fache der Rücklaufzeit einzustellen (Bereich 40 … 110 s). Bei 60 s darf der Behang höchstens 40 s fahren. WAREMA nennt für Raffstoren ca. 70 s je 2,5 m – das entspricht etwa 1,4 m Behanghöhe.",
"4.  Gesamtzeit: Nach den 60 s brauchen die D+H-Antriebe im RWA-Schnelllauf bis zu weitere 60 s bis zur Öffnungsposition.",
"5.  K1 ersetzt das D+H-Schaltrelais TRL 8 aus dem AT-41-Plan, weil das WAREMA-Kit die Umschaltung selbst übernimmt. Speiseklemme „+“ (Ext. Kl. 4), Abgriff A / S / – an der Stiftleiste und deren Belastbarkeit mit D+H bestätigen.",
"6.  WAREMA: Eingang In3 mit Kodierschalter S1 = ON auf niedrige Priorität stellen, sonst ist nach jeder Lüftung ein Reset nötig. In1 (USV-Störung) bleibt hohe Priorität, deshalb der Schlüsseltaster. Bei Netzausfall fahren die Behänge nach 25 s selbst hoch.",
"7.  WAREMA bezeichnet das Notstrom-Kit als Komfortsteuerung; der Einsatz muss über das Brandschutzkonzept vom Verantwortlichen nach § 66 MBO freigegeben werden. Nur 230-V-Antriebe, ein Antrieb je MSE, je USV 4 × 100 W, 3 × 140 W oder 2 × 200 W.",
"8.  Positionsschalter B1 / B2 sind kein WAREMA-Serienteil; das Kit meldet nur Störung (X6), nicht die Endlage. Bei Frost kann ein angefrorener Behang nicht hochfahren.",
"9.  Koppelrelais K1 … K5 mit Kontakten für Kleinsignale (24 V DC, 1–2 mA) wählen. Leitungen mit Funktionserhalt nach Brandschutzkonzept und Leitungsanlagen-Richtlinie.",
"10. Abnahme: Gesamtablauf bei Netzbetrieb und bei Netzausfall mit Zeitmessung prüfen; Rückstellung an RWA-Zentrale / RT 45 und an den Schlüsseltastern.",]
lines(56,1436,notes,11.5,28)
a('</svg>')
open(sys.argv[1],"w").write("\n".join(o))
