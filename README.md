# Discovery Engine V0

เครื่องยนต์ค้นพบแบบ event-sourced ไม่มี LLM ในวงจร ใช้ simulation เป็นผู้ตัดสิน
เป้าหมายของ V0: ค้นพบ phase transition ของ Ising 2 มิติเอง (Tc, เลขชี้กำลัง) โดยไม่ถูกบอกค่า

## เริ่มจากมือถือ (ไม่มีค่าใช้จ่าย)

1. สร้าง repo ใหม่บน GitHub แบบ **Public** (Actions บน public repo ฟรีไม่จำกัดนาที)
2. เพิ่มไฟล์ผ่านเว็บ GitHub: Add file > Create new file แล้วตั้งชื่อและวางเนื้อหา
   - `engine.py`
   - `.github/workflows/worker.yml` (พิมพ์ชื่อพร้อม / จะสร้างโฟลเดอร์ให้เอง)
   - `.gitignore`, `README.md`
3. แท็บ Actions > discovery-worker > Run workflow
4. รอจบ แล้วเปิด `report.md` ใน repo; รันซ้ำเพื่อให้ระบบเลือกการทดลองรอบถัดไป
   (ตั้งให้รันเองทุก 6 ชั่วโมงอยู่แล้ว)

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

ตรวจ 7 ข้อ: hash chain จับการแก้ไข, ผลซ้ำได้, kill -9 แล้ว resume, engine สองตัวตรงกัน,
null battery (ระบบที่ไม่มี transition ต้องไม่ถูกบอกว่ามี), planted law (J=1.5 แล้ว Tc ต้องเลื่อนตาม),
known answer (ค้นพบ Tc และเลขชี้กำลังใกล้ค่าที่รู้)

## ข้อจำกัดของ V0 (ตั้งใจให้ชัด)

- null ของ V0 คือสปินสุ่มอิสระ ยังไม่ใช่ surrogate ที่รักษา autocorrelation
- R2 เขียนโดยผู้เขียนเดียวกับ engine หลัก ไม่ใช่ clean-room (R3)
- observable ที่ใช้เป็นชุด moment ทั่วไปที่เขียนไว้ล่วงหน้า ยังไม่ใช่การค้นหา observable เอง
- ขนาด L = 8, 16, 32 เท่านั้น ผลทั้งหมดเป็น simulation truth ภายใต้ model นี้
