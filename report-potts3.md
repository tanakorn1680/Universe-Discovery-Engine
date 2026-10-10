# รายงาน Discovery Engine V0 — Potts q=3 2D

สร้างเมื่อ 2026-10-10 11:07:46 UTC · code `45ff4d02ba6ddfab`

ผลทั้งหมดนี้เป็นผลของ simulation ภายใต้ model และ assumption ด้านล่าง ไม่ใช่กฎของธรรมชาติ

## สถานะ
- events 1273 รายการ · hash chain: ผ่าน
- jobs: done 423

## ผลการประมาณ (substrate: Potts q=3 2D, ขนาด L = 8, 16, 32)

| ปริมาณ | ค่าประมาณ | 95% CI (bootstrap) |
|---|---|---|
| T ที่ susceptibility สูงสุด (เฉลี่ยทุกขนาด) | 1.030 | 1.016 ถึง 1.038 |
| T ที่เส้น Binder ตัดกัน | 0.986 | 0.976 ถึง 0.996 |
| เลขชี้กำลังของยอด susceptibility เทียบ L | 1.859 | 1.111 ถึง 1.984 |
| เลขชี้กำลังของ order statistic ที่จุดตัด | 0.095 | 0.070 ถึง 0.178 |

## คำตัดสิน
พบ candidate ของ phase transition ระดับ **L2 (ชั่วคราว)**: ยอด susceptibility สูงขึ้นตามขนาดระบบ (CI ล่างของเลขชี้กำลัง > 0.5) และเส้น Binder ตัดกัน

L2 ต้องผ่าน R2 และมี ≥ 3 ขนาด; R2 ที่นี่เขียนโดยผู้เขียนเดียวกัน ไม่ใช่ clean-room (R3) จึงเป็น L2 แบบชั่วคราว ยังไม่มีสิทธิ์ขึ้น L3

## R2: reference engine เทียบ fast engine (L=8)
ผล: ผ่าน (เกณฑ์ |z| < 5)

| L | T | observable | ref | fast | z |
|---|---|---|---|---|---|
| 8 | 0.7 | absm | 0.9851 | 0.9860 | 1.31 |
| 8 | 0.7 | e | -1.9638 | -1.9660 | 1.47 |
| 8 | 1.0 | absm | 0.7510 | 0.7886 | 2.29 |
| 8 | 1.0 | e | -1.6300 | -1.6644 | 2.33 |
| 8 | 1.3 | absm | 0.2690 | 0.2685 | 0.08 |
| 8 | 1.3 | e | -1.1404 | -1.1372 | 0.69 |

## Assumption ledger
- กฎ: Metropolis บน Potts q=3 2D, J=1, boundary แบบ periodic, เริ่มจากสถานะเรียงตัวทั้งหมด
- ขนาด L = 8, 16, 32 เท่านั้น (finite-size effects ยังมีผล)
- ตารางอุณหภูมิถูกเลือกโดย selector ตามเหตุผลในหัวข้อถัดไป
- analysis ไม่ได้รับค่าที่รู้ล่วงหน้าใด ๆ; ใช้ moment ทั่วไปของสถานะ (ค่าเฉลี่ย |m|, m², m⁴)

## เทียบความรู้เดิม (ทำหลังวิเคราะห์ ระบบไม่ได้ถูกบอกค่าเหล่านี้)
- ค่า exact ของ Potts q=3 2D: Tc = 0.9950, γ/ν = 1.7333, β/ν = 0.1333
- ความต่างจากค่าประมาณ: Tc -0.009, γ/ν 0.125, β/ν -0.038

## ประวัติการเลือกการทดลอง
- stage 1 [Potts q=3 2D]: no data yet -> space-filling coarse sweep T=0.7..1.3 (step 0.05), 3 sizes x 3 seeds, plus reference-engine jobs (L=8) for the R2 check (126 jobs)
- stage 2 [Potts q=3 2D]: susceptibility-like peak sits near T=1.030 on the coarse grid -> refine +-0.1 around it (11 points) to resolve the peak and the crossings (99 jobs)
- stage 3: exponent CI width 0.97 > 0.35 -> 3 more seeds at the refined temperatures to cut statistical error (99 jobs)
- stage 4: exponent CI width 0.85 > 0.35 -> 3 more seeds at the refined temperatures to cut statistical error (99 jobs)
