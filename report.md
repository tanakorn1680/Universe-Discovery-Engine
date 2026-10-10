# รายงาน Discovery Engine V0 — Ising 2D

สร้างเมื่อ 2026-10-10 05:58:39 UTC · code `45ff4d02ba6ddfab`

ผลทั้งหมดนี้เป็นผลของ simulation ภายใต้ model และ assumption ด้านล่าง ไม่ใช่กฎของธรรมชาติ

## สถานะ
- events 677 รายการ · hash chain: ผ่าน
- jobs: done 225

## ผลการประมาณ (substrate: Ising 2D, ขนาด L = 8, 16, 32)

| ปริมาณ | ค่าประมาณ | 95% CI (bootstrap) |
|---|---|---|
| T ที่ susceptibility สูงสุด (เฉลี่ยทุกขนาด) | 2.429 | 2.386 ถึง 2.445 |
| T ที่เส้น Binder ตัดกัน | 2.271 | 2.261 ถึง 2.284 |
| เลขชี้กำลังของยอด susceptibility เทียบ L | 1.764 | 1.717 ถึง 1.916 |
| เลขชี้กำลังของ order statistic ที่จุดตัด | 0.118 | 0.102 ถึง 0.149 |

## คำตัดสิน
พบ candidate ของ phase transition ระดับ **L2 (ชั่วคราว)**: ยอด susceptibility สูงขึ้นตามขนาดระบบ (CI ล่างของเลขชี้กำลัง > 0.5) และเส้น Binder ตัดกัน

L2 ต้องผ่าน R2 และมี ≥ 3 ขนาด; R2 ที่นี่เขียนโดยผู้เขียนเดียวกัน ไม่ใช่ clean-room (R3) จึงเป็น L2 แบบชั่วคราว ยังไม่มีสิทธิ์ขึ้น L3

## R2: reference engine เทียบ fast engine (L=8)
ผล: ผ่าน (เกณฑ์ |z| < 5)

| L | T | observable | ref | fast | z |
|---|---|---|---|---|---|
| 8 | 1.8 | absm | 0.9561 | 0.9575 | 1.57 |
| 8 | 1.8 | e | -1.8567 | -1.8605 | 1.84 |
| 8 | 2.3 | absm | 0.7533 | 0.7574 | 0.52 |
| 8 | 2.3 | e | -1.4527 | -1.4554 | 0.29 |
| 8 | 3.0 | absm | 0.3461 | 0.3403 | 1.24 |
| 8 | 3.0 | e | -0.8438 | -0.8357 | 1.63 |

## Assumption ledger
- กฎ: Metropolis บน Ising 2D, J=1, boundary แบบ periodic, เริ่มจากสถานะเรียงตัวทั้งหมด
- ขนาด L = 8, 16, 32 เท่านั้น (finite-size effects ยังมีผล)
- ตารางอุณหภูมิถูกเลือกโดย selector ตามเหตุผลในหัวข้อถัดไป
- analysis ไม่ได้รับค่าที่รู้ล่วงหน้าใด ๆ; ใช้ moment ทั่วไปของสถานะ (ค่าเฉลี่ย |m|, m², m⁴)

## เทียบความรู้เดิม (ทำหลังวิเคราะห์ ระบบไม่ได้ถูกบอกค่าเหล่านี้)
- ค่า exact ของ Ising 2D: Tc = 2.2692, γ/ν = 1.7500, β/ν = 0.1250
- ความต่างจากค่าประมาณ: Tc 0.002, γ/ν 0.014, β/ν -0.007

## ประวัติการเลือกการทดลอง
- stage 1: no data yet -> space-filling coarse sweep T=1.8..3.0 (step 0.1), 3 sizes x 3 seeds, plus reference-engine jobs (L=8) for the R2 check (126 jobs)
- stage 2: susceptibility-like peak sits near T=2.412 on the coarse grid -> refine +-0.2 around it (11 points) to resolve the peak and the crossings (99 jobs)
