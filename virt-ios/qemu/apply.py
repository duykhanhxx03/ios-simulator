#!/usr/bin/env python3
"""Apply virt-ios additions to a QEMU source tree (idempotent).

  apply.py QEMU_SRC

- hw/misc/sweet_ufs.c, hw/display/sweet_fb.c (copied from this directory)
- meson.build entries for both
- virt machine property  sweet=on : maps sweet-ufshc at 0x0b000000 (drive id
  "ufs0", SPI 110) and a sweet-fb console over guest RAM at 0x9c000000.
"""
import os, shutil, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = sys.argv[1]


def edit(path, anchor, text, after=True, marker=None):
    p = os.path.join(SRC, path)
    s = open(p).read()
    if (marker or text) in s:
        return
    if anchor not in s:
        sys.exit(f'{path}: anchor not found: {anchor!r}')
    s = s.replace(anchor, anchor + text if after else text + anchor, 1)
    open(p, 'w').write(s)
    print(f'patched {path}')


for rel in ('hw/misc/sweet_ufs.c', 'hw/display/sweet_fb.c'):
    shutil.copy(os.path.join(HERE, rel), os.path.join(SRC, rel))
    print(f'copied {rel}')

edit('hw/misc/meson.build', "system_ss.add(when: 'CONFIG_UNIMP', if_true: files('unimp.c'))\n",
     "system_ss.add(when: 'CONFIG_ARM_VIRT', if_true: files('sweet_ufs.c'))\n")
edit('hw/display/meson.build', "stub_ss.add(files('ramfb-stubs.c'))\n",
     "system_ss.add(when: 'CONFIG_ARM_VIRT', if_true: files('sweet_fb.c'))\n")

# VirtMachineState field
edit('include/hw/arm/virt.h', '    bool mte;\n', '    bool sweet;\n')

# device creation, right after the platform bus
edit('hw/arm/virt.c', '    create_platform_bus(vms);\n', '''
    if (vms->sweet) {
        /* virt-ios: devices the ios-sweet XNU kernel expects (see virt-ios/) */
        BlockBackend *ufs_blk = blk_by_name("ufs0");
        if (!ufs_blk) {
            error_report("sweet=on needs -drive if=none,id=ufs0,file=...");
            exit(1);
        }
        DeviceState *ufs = qdev_new("sweet-ufshc");
        qdev_prop_set_drive_err(ufs, "drive", ufs_blk, &error_fatal);
        sysbus_realize_and_unref(SYS_BUS_DEVICE(ufs), &error_fatal);
        sysbus_mmio_map(SYS_BUS_DEVICE(ufs), 0, 0x0b000000);
        sysbus_connect_irq(SYS_BUS_DEVICE(ufs), 0, qdev_get_gpio_in(vms->gic, 110));

        DeviceState *fb = qdev_new("sweet-fb");
        sysbus_realize_and_unref(SYS_BUS_DEVICE(fb), &error_fatal);
    }
''', marker='virt-ios: devices')

# property accessors, before virt_get_its
edit('hw/arm/virt.c', 'static bool virt_get_its(Object *obj, Error **errp)\n', '''static bool virt_get_sweet(Object *obj, Error **errp)
{
    return VIRT_MACHINE(obj)->sweet;
}

static void virt_set_sweet(Object *obj, bool value, Error **errp)
{
    VIRT_MACHINE(obj)->sweet = value;
}

''', after=False, marker='virt_get_sweet(Object')

edit('hw/arm/virt.c', '    object_class_property_add_bool(oc, "its", virt_get_its,\n', '''    object_class_property_add_bool(oc, "sweet", virt_get_sweet,
                                   virt_set_sweet);
    object_class_property_set_description(oc, "sweet",
                                          "virt-ios: add the ios-sweet UFS host "
                                          "(drive ufs0) and RAM framebuffer");

''', after=False, marker='"sweet", virt_get_sweet')

edit('hw/arm/virt.c', '#include "qemu/guest-random.h"\n',
     '#include "system/block-backend.h"\n#include "hw/core/qdev-properties-system.h"\n',
     marker='#include "system/block-backend.h"')

# SMP like the phone: ios-sweet starts secondaries with PSCI CPU_ON over `smc #0`
# (patch 0065) and numbers cores by MPIDR Aff1 (core << 8, patch 0078).
edit('hw/arm/virt.c', '''    uint8_t clustersz;

    /*
     * Adjust MPIDR to make TCG consistent''', '''    if (vms->sweet) {
        return (uint64_t)idx << 8;      /* virt-ios: Aff1 = core, as on SM7150 */
    }
''', after=False, marker='virt-ios: Aff1 = core')
edit('hw/arm/virt.c', '''    } else {
        vms->psci_conduit = QEMU_PSCI_CONDUIT_HVC;
    }
''', '''    if (vms->sweet) {
        vms->psci_conduit = QEMU_PSCI_CONDUIT_SMC;   /* virt-ios: ios-sweet uses smc */
    }
''', marker='virt-ios: ios-sweet uses smc')
print('done')
