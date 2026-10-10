# Discovery Engine V1.2

เครื่องยนต์ค้นพบแบบ event-sourced ไม่มี LLM ในวงจร ใช้ simulation เป็นผู้ตัดสิน
เป้าหมายของ V0: ค้นพบ phase transition (Tc, เลขชี้กำลัง) เอง โดยไม่ถูกบอกค่า

มี 3 โจทย์ รันต่อกันในแต่ละรอบ:
- `ising` : Ising 2 มิติ (โจทย์ที่ใช้ปรับเกณฑ์ตอนสร้างระบบ) ผลอยู่ใน `report.md`
- `potts3`: Potts q=3 (held-out: ระบบไม่เคยถูกปรับด้วยโจทย์นี้) ผลอยู่ใน `report-potts3.md`
- `pca`   : ระบบลองกฎ cellular automaton ที่มีสัญญาณรบกวน 32 แบบเอง แล้วบอกว่ากฎไหนมีจุดเปลี่ยนสถานะ
            และเข้ากับตระกูลที่รู้จักหรือไม่ ผลอยู่ใน `report-pca.md` (รอบแรกใช้เวลานานกว่าโจทย์อื่น)
            รอบหลัง ๆ จะเอากฎที่น่าสงสัยไปรันในระบบใหญ่ขึ้น (L = 64, 128) เพื่อดูว่าความแปลกเป็นแค่ผลของระบบเล็กหรือไม่
            แล้วซูมตารางสัญญาณรบกวนให้ละเอียดขึ้นตามขนาด (ยอดของ susceptibility แคบลงเมื่อระบบใหญ่ขึ้น)
            ใช้หลายรอบ กด Run workflow ซ้ำจนระบบบอกว่า converged

## เริ่มจากมือถือ (ไม่มีค่าใช้จ่าย)

1. สร้าง repo ใหม่บน GitHub แบบ **Public** (Actions บน public repo ฟรีไม่จำกัดนาที)
2. เพิ่มไฟล์ผ่านเว็บ GitHub: Add file > Create new file แล้วตั้งชื่อและวางเนื้อหา
   - `engine.py`
   - `.github/workflows/worker.yml` (พิมพ์ชื่อพร้อม / จะสร้างโฟลเดอร์ให้เอง)
   - `.gitignore`, `README.md`
3. แท็บ Actions > discovery-worker > Run workflow
4. รอจบ แล้วเปิด `report.md` และ `report-potts3.md` ใน repo; รันซ้ำเพื่อให้ระบบเลือกการทดลองรอบถัดไป
   (ตั้งให้รันเองทุก 6 ชั่วโมงอยู่แล้ว)
5. ถ้าอัปเดตจากเวอร์ชันก่อน: ทับ `engine.py` และ `.github/workflows/worker.yml` ได้เลย
   state เดิมของ Ising ใช้ต่อได้ (job เดิมไม่ถูกรันซ้ำ)

## แต่ละรอบทำอะไร

`verify` ตรวจ hash chain -> `plan` เลือกการทดลองถัดไปและบันทึกเหตุผล -> `work` รัน jobs
-> `analyze` ประมาณค่าพร้อม bootstrap CI -> commit state กลับเข้า repo

- รอบ 1: กวาดอุณหภูมิหยาบ 3 ขนาดระบบ + งานตรวจ engine ที่สอง (R2)
- รอบ 2: ซูมรอบจุดที่ susceptibility สูงสุด
- รอบ 3+: เพิ่ม seed ถ้า CI ของเลขชี้กำลังยังกว้าง; ถ้าพอแล้วจะบอกว่าไม่มีอะไรต้องทำเพิ่ม

## ความทนทาน

state ทั้งหมดอยู่ใน `state/events.jsonl` (hash chain, append-only) กับ `state/artifacts/`
ถ้าโปรเซสถูกตัดกลางคัน (หมดเวลา, kill -9) แค่รันใหม่ มันอ่าน log แล้วทำต่อ

## ทดสอบระบบเอง

    python engine.py selftest          # ใช้เวลาไม่กี่นาที
    python engine.py selftest --quick  # เวอร์ชันสั้น

ตรวจ 10 ข้อ: hash chain จับการแก้ไข, ผลซ้ำได้, kill -9 แล้ว resume, engine สองตัวตรงกัน,
null battery (ระบบที่ไม่มี transition ต้องไม่ถูกบอกว่ามี), planted law (J=1.5 แล้ว Tc ต้องเลื่อนตาม),
known answer (ค้นพบ Tc และเลขชี้กำลังใกล้ค่าที่รู้) held-out Potts q=3 และกฎ CA (engine ที่สองตรงกัน, กฎที่ไม่มีทางเรียงตัวต้องไม่ถูกบอกว่ามีจุดเปลี่ยน)

## ข้อจำกัดของ V0 (ตั้งใจให้ชัด)

- null ของ V0 คือสปินสุ่มอิสระ ยังไม่ใช่ surrogate ที่รักษา autocorrelation
- R2 เขียนโดยผู้เขียนเดียวกับ engine หลัก ไม่ใช่ clean-room (R3)
- observable ที่ใช้เป็นชุด moment ทั่วไปที่เขียนไว้ล่วงหน้า ยังไม่ใช่การค้นหา observable เอง
- ขนาด L = 8, 16, 32 เท่านั้น ผลทั้งหมดเป็น simulation truth ภายใต้ model นี้
