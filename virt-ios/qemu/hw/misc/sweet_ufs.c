/*
 * Minimal UFS host controller (UFSHCI 2.1 register subset) for virt-ios.
 *
 * Models what the ios-sweet XNU driver (bsd/dev/sweet_ufs.c) expects from the
 * Qualcomm UFS host that the phone's bootloader leaves running:
 *   - HCE = 1, HCS = DP | UTRLRDY at reset (link already up)
 *   - transfer request list in guest memory (UTRLBA/UTRLBAU), doorbell UTRLDBR
 *   - COMMAND UPIUs carrying SCSI CDBs, data through the PRDT
 *   - 4096-byte logical blocks on LUN 0
 * Requests complete synchronously when the doorbell is rung; IS.UTRCS is set
 * and the (optional) interrupt raised if IE.UTRCE is enabled.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "qapi/error.h"
#include "hw/core/sysbus.h"
#include "hw/core/irq.h"
#include "hw/core/qdev-properties.h"
#include "hw/core/qdev-properties-system.h"
#include "system/block-backend.h"
#include "system/dma.h"
#include "system/address-spaces.h"
#include "migration/blocker.h"

#define TYPE_SWEET_UFSHC "sweet-ufshc"
OBJECT_DECLARE_SIMPLE_TYPE(SweetUFSState, SWEET_UFSHC)

#define REG_CAP      0x00
#define REG_VER      0x08
#define REG_IS       0x20
#define REG_IE       0x24
#define REG_HCS      0x30
#define REG_HCE      0x34
#define REG_UTRLBA   0x50
#define REG_UTRLBAU  0x54
#define REG_UTRLDBR  0x58
#define REG_UTRLCLR  0x5c
#define REG_UTRLRSR  0x60
#define REG_UTMRLBA  0x70
#define REG_UTMRLBAU 0x74
#define REG_UTMRLRSR 0x80

#define NUTRS        32
#define CAP_64AS     (1u << 24)
#define HCS_READY    0xfu           /* DP | UTRLRDY | UTMRLRDY | UCRDY */
#define IS_UTRCS     (1u << 0)

#define UFS_BLOCK    4096u
#define MMIO_SIZE    0x3000

/* SCSI status / sense */
#define ST_GOOD      0x00
#define ST_CHECK     0x02
#define SK_ILLEGAL   0x05
#define SK_MEDIUM    0x03

struct SweetUFSState {
    SysBusDevice parent_obj;
    MemoryRegion mmio;
    qemu_irq irq;
    BlockBackend *blk;
    AddressSpace *as;
    uint64_t nblocks;

    uint32_t is, ie, hce;
    uint32_t utrlba, utrlbau, utrldbr, utrlrsr;
    uint32_t utmrlba, utmrlbau, utmrlrsr;
    uint8_t *buf;                   /* data staging buffer */
    uint32_t buf_size;
    Error *migration_blocker;
};

static void sweet_ufs_update_irq(SweetUFSState *s)
{
    qemu_set_irq(s->irq, !!(s->is & s->ie));
}

/* Walk the PRDT, moving len bytes between s->buf and guest memory. */
static bool prdt_xfer(SweetUFSState *s, uint64_t ucd, uint32_t prdt_off_dw,
                      uint32_t nprdt, uint32_t len, bool to_guest)
{
    uint32_t done = 0;
    for (uint32_t i = 0; i < nprdt && done < len; i++) {
        uint32_t e[4];
        if (dma_memory_read(s->as, ucd + prdt_off_dw * 4 + i * 16, e, sizeof(e),
                            MEMTXATTRS_UNSPECIFIED) != MEMTX_OK) {
            return false;
        }
        uint64_t addr = ((uint64_t)le32_to_cpu(e[1]) << 32) | le32_to_cpu(e[0]);
        uint32_t n = (le32_to_cpu(e[3]) & 0x3ffff) + 1;
        n = MIN(n, len - done);
        MemTxResult r = to_guest
            ? dma_memory_write(s->as, addr, s->buf + done, n, MEMTXATTRS_UNSPECIFIED)
            : dma_memory_read(s->as, addr, s->buf + done, n, MEMTXATTRS_UNSPECIFIED);
        if (r != MEMTX_OK) {
            return false;
        }
        done += n;
    }
    return done == len;
}

static void ensure_buf(SweetUFSState *s, uint32_t len)
{
    if (len > s->buf_size) {
        s->buf = g_realloc(s->buf, len);
        s->buf_size = len;
    }
}

/* Execute one CDB. Returns the SCSI status; fills sense on CHECK CONDITION. */
static uint8_t scsi_exec(SweetUFSState *s, const uint8_t *cdb, uint32_t xfer_len,
                         uint64_t ucd, uint32_t prdt_off, uint32_t nprdt,
                         uint8_t *sense, uint32_t *resid)
{
    uint64_t lba = 0;
    uint32_t count = 0;
    bool write = false;
    *resid = 0;

#define CHECK(key, asc) do { sense[0] = 0x70; sense[2] = (key); sense[7] = 10; \
                             sense[12] = (asc); return ST_CHECK; } while (0)

    switch (cdb[0]) {
    case 0x00: /* TEST UNIT READY */
    case 0x1b: /* START STOP UNIT */
        return ST_GOOD;
    case 0x35: /* SYNCHRONIZE CACHE(10) */
    case 0x91: /* SYNCHRONIZE CACHE(16) */
        blk_flush(s->blk);
        return ST_GOOD;
    case 0x12: { /* INQUIRY */
        uint8_t inq[36] = { 0x00, 0x00, 0x06, 0x02, 31 };
        memcpy(&inq[8], "VIRT-IOS", 8);
        memcpy(&inq[16], "SWEET UFS DISK  ", 16);
        memcpy(&inq[32], "0001", 4);
        uint32_t n = MIN(xfer_len, (uint32_t)sizeof(inq));
        ensure_buf(s, n);
        memcpy(s->buf, inq, n);
        if (n && !prdt_xfer(s, ucd, prdt_off, nprdt, n, true)) {
            CHECK(SK_MEDIUM, 0x11);
        }
        *resid = xfer_len - n;
        return ST_GOOD;
    }
    case 0x25: { /* READ CAPACITY(10) */
        uint8_t cap[8];
        stl_be_p(cap, s->nblocks > 0xffffffffull ? 0xffffffff : (uint32_t)(s->nblocks - 1));
        stl_be_p(cap + 4, UFS_BLOCK);
        uint32_t n = MIN(xfer_len, 8u);
        ensure_buf(s, 8);
        memcpy(s->buf, cap, 8);
        if (n && !prdt_xfer(s, ucd, prdt_off, nprdt, n, true)) {
            CHECK(SK_MEDIUM, 0x11);
        }
        return ST_GOOD;
    }
    case 0x9e: { /* SERVICE ACTION IN: READ CAPACITY(16) */
        uint8_t cap[32] = { 0 };
        stq_be_p(cap, s->nblocks - 1);
        stl_be_p(cap + 8, UFS_BLOCK);
        uint32_t n = MIN(xfer_len, 32u);
        ensure_buf(s, 32);
        memcpy(s->buf, cap, 32);
        if (n && !prdt_xfer(s, ucd, prdt_off, nprdt, n, true)) {
            CHECK(SK_MEDIUM, 0x11);
        }
        return ST_GOOD;
    }
    case 0x2a: /* WRITE(10) */
        write = true;
        /* fall through */
    case 0x28: /* READ(10) */
        lba = ldl_be_p(&cdb[2]);
        count = lduw_be_p(&cdb[7]);
        break;
    case 0x8a: /* WRITE(16) */
        write = true;
        /* fall through */
    case 0x88: /* READ(16) */
        lba = ldq_be_p(&cdb[2]);
        count = ldl_be_p(&cdb[10]);
        break;
    default:
        qemu_log_mask(LOG_UNIMP, "sweet-ufshc: unsupported SCSI op 0x%02x\n", cdb[0]);
        CHECK(SK_ILLEGAL, 0x20);
    }

    if (lba + count > s->nblocks) {
        CHECK(SK_ILLEGAL, 0x21);
    }
    uint64_t bytes = (uint64_t)count * UFS_BLOCK;
    if (bytes > xfer_len || bytes > (64u << 20)) {
        CHECK(SK_ILLEGAL, 0x24);
    }
    if (!bytes) {
        return ST_GOOD;
    }
    ensure_buf(s, bytes);
    if (write) {
        if (!prdt_xfer(s, ucd, prdt_off, nprdt, bytes, false) ||
            blk_pwrite(s->blk, lba * UFS_BLOCK, bytes, s->buf, 0) < 0) {
            CHECK(SK_MEDIUM, 0x0c);
        }
    } else {
        if (blk_pread(s->blk, lba * UFS_BLOCK, bytes, s->buf, 0) < 0 ||
            !prdt_xfer(s, ucd, prdt_off, nprdt, bytes, true)) {
            CHECK(SK_MEDIUM, 0x11);
        }
    }
    *resid = xfer_len - bytes;
    return ST_GOOD;
#undef CHECK
}

/* Process one transfer request slot. */
static void process_slot(SweetUFSState *s, unsigned slot)
{
    uint64_t list = ((uint64_t)s->utrlbau << 32) | s->utrlba;
    uint64_t utrd_pa = list + slot * 32;
    uint32_t d[8];
    uint8_t ocs = 0;

    if (dma_memory_read(s->as, utrd_pa, d, sizeof(d), MEMTXATTRS_UNSPECIFIED) != MEMTX_OK) {
        return;
    }
    for (int i = 0; i < 8; i++) {
        d[i] = le32_to_cpu(d[i]);
    }
    uint64_t ucd = ((uint64_t)d[5] << 32) | (d[4] & ~0x7fu);
    uint32_t resp_off = d[6] >> 16, resp_len = d[6] & 0xffff;   /* DWORDs */
    uint32_t prdt_off = d[7] >> 16, nprdt = d[7] & 0xffff;

    uint8_t cmd[32];
    uint8_t resp[64] = { 0 };
    if (dma_memory_read(s->as, ucd, cmd, sizeof(cmd), MEMTXATTRS_UNSPECIFIED) != MEMTX_OK) {
        ocs = 0x05; /* MISMATCH_REQUEST_SIZE as generic failure */
        goto done;
    }

    switch (cmd[0] & 0x3f) {
    case 0x00: /* NOP OUT -> NOP IN */
        resp[0] = 0x20;
        resp[3] = cmd[3];
        break;
    case 0x01: { /* COMMAND */
        uint8_t sense[18] = { 0 };
        uint32_t resid = 0;
        uint32_t xfer_len = ldl_be_p(&cmd[12]);
        uint8_t status = cmd[2] == 0
            ? scsi_exec(s, &cmd[16], xfer_len, ucd, prdt_off, nprdt, sense, &resid)
            : ST_CHECK;
        resp[0] = 0x21;
        resp[2] = cmd[2];
        resp[3] = cmd[3];
        resp[6] = 0x00;     /* target success */
        resp[7] = status;
        if (resid) {
            resp[1] |= 0x20; /* U: underflow */
            stl_be_p(&resp[12], resid);
        }
        if (status == ST_CHECK) {
            stw_be_p(&resp[10], 2 + sizeof(sense));
            stw_be_p(&resp[32], sizeof(sense));
            memcpy(&resp[34], sense, sizeof(sense));
        }
        break;
    }
    default:
        qemu_log_mask(LOG_UNIMP, "sweet-ufshc: unsupported UPIU 0x%02x\n", cmd[0]);
        ocs = 0x01; /* INVALID_COMMAND_TABLE_ATTRIBUTES */
        goto done;
    }

    if (resp_len) {
        uint32_t n = MIN(resp_len * 4, (uint32_t)sizeof(resp));
        dma_memory_write(s->as, ucd + resp_off * 4, resp, n, MEMTXATTRS_UNSPECIFIED);
    }
done:
    d[2] = (d[2] & ~0xffu) | ocs;
    uint32_t dw2 = cpu_to_le32(d[2]);
    dma_memory_write(s->as, utrd_pa + 8, &dw2, 4, MEMTXATTRS_UNSPECIFIED);
}

static void ring_doorbell(SweetUFSState *s, uint32_t bits)
{
    if (!(s->utrlrsr & 1) || !s->blk) {
        return;
    }
    s->utrldbr |= bits;
    for (unsigned slot = 0; slot < NUTRS; slot++) {
        if (s->utrldbr & (1u << slot)) {
            process_slot(s, slot);
            s->utrldbr &= ~(1u << slot);
        }
    }
    s->is |= IS_UTRCS;
    sweet_ufs_update_irq(s);
}

static uint64_t sweet_ufs_read(void *opaque, hwaddr off, unsigned size)
{
    SweetUFSState *s = opaque;
    switch (off) {
    case REG_CAP:     return CAP_64AS | (NUTRS - 1);
    case REG_VER:     return 0x210;
    case REG_IS:      return s->is;
    case REG_IE:      return s->ie;
    case REG_HCS:     return s->hce ? HCS_READY : 0;
    case REG_HCE:     return s->hce;
    case REG_UTRLBA:  return s->utrlba;
    case REG_UTRLBAU: return s->utrlbau;
    case REG_UTRLDBR: return s->utrldbr;
    case REG_UTRLRSR: return s->utrlrsr;
    case REG_UTMRLBA: return s->utmrlba;
    case REG_UTMRLBAU: return s->utmrlbau;
    case REG_UTMRLRSR: return s->utmrlrsr;
    default:
        return 0;
    }
}

static void sweet_ufs_write(void *opaque, hwaddr off, uint64_t val, unsigned size)
{
    SweetUFSState *s = opaque;
    switch (off) {
    case REG_IS:      s->is &= ~(uint32_t)val; sweet_ufs_update_irq(s); break;
    case REG_IE:      s->ie = val; sweet_ufs_update_irq(s); break;
    case REG_HCE:     s->hce = val & 1; break;
    case REG_UTRLBA:  s->utrlba = val & ~0x3ffu; break;
    case REG_UTRLBAU: s->utrlbau = val; break;
    case REG_UTRLDBR: ring_doorbell(s, val); break;
    case REG_UTRLCLR: s->utrldbr &= val; break;
    case REG_UTRLRSR: s->utrlrsr = val & 1; break;
    case REG_UTMRLBA: s->utmrlba = val & ~0x3ffu; break;
    case REG_UTMRLBAU: s->utmrlbau = val; break;
    case REG_UTMRLRSR: s->utmrlrsr = val & 1; break;
    default:
        qemu_log_mask(LOG_UNIMP, "sweet-ufshc: write 0x%" HWADDR_PRIx " = 0x%" PRIx64 "\n",
                      off, val);
        break;
    }
}

static const MemoryRegionOps sweet_ufs_ops = {
    .read = sweet_ufs_read,
    .write = sweet_ufs_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
};

static void sweet_ufs_reset(DeviceState *dev)
{
    SweetUFSState *s = SWEET_UFSHC(dev);
    /* As the bootloader leaves it: enabled, link up, lists ready. */
    s->hce = 1;
    s->is = s->ie = 0;
    s->utrldbr = 0;
    s->utrlrsr = 1;
    s->utmrlrsr = 1;
}

static void sweet_ufs_realize(DeviceState *dev, Error **errp)
{
    SweetUFSState *s = SWEET_UFSHC(dev);
    if (!s->blk) {
        error_setg(errp, "sweet-ufshc: drive property not set");
        return;
    }
    int64_t len = blk_getlength(s->blk);
    if (len < 0 || len % UFS_BLOCK) {
        error_setg(errp, "sweet-ufshc: drive size must be a multiple of %u", UFS_BLOCK);
        return;
    }
    if (!blk_supports_write_perm(s->blk)) {
        error_setg(errp, "sweet-ufshc: drive is read-only");
        return;
    }
    if (blk_set_perm(s->blk, BLK_PERM_CONSISTENT_READ | BLK_PERM_WRITE,
                     BLK_PERM_ALL, errp) < 0) {
        return;
    }
    s->nblocks = len / UFS_BLOCK;
    s->as = &address_space_memory;
    memory_region_init_io(&s->mmio, OBJECT(dev), &sweet_ufs_ops, s,
                          TYPE_SWEET_UFSHC, MMIO_SIZE);
    sysbus_init_mmio(SYS_BUS_DEVICE(dev), &s->mmio);
    sysbus_init_irq(SYS_BUS_DEVICE(dev), &s->irq);
    error_setg(&s->migration_blocker, "sweet-ufshc does not support migration");
    migrate_add_blocker(&s->migration_blocker, errp);
}

static const Property sweet_ufs_props[] = {
    DEFINE_PROP_DRIVE("drive", SweetUFSState, blk),
};

static void sweet_ufs_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    dc->realize = sweet_ufs_realize;
    device_class_set_legacy_reset(dc, sweet_ufs_reset);
    device_class_set_props(dc, sweet_ufs_props);
    dc->desc = "Minimal UFS host controller for ios-sweet XNU";
    set_bit(DEVICE_CATEGORY_STORAGE, dc->categories);
}

static const TypeInfo sweet_ufs_info = {
    .name = TYPE_SWEET_UFSHC,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(SweetUFSState),
    .class_init = sweet_ufs_class_init,
};

static void sweet_ufs_register(void)
{
    type_register_static(&sweet_ufs_info);
}
type_init(sweet_ufs_register)
