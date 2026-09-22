"""Build tenant-native widgets from user-exported input/table widget files."""
import argparse
import copy
import json
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def build(input_export, table_export, output):
    output.mkdir(parents=True, exist_ok=True)
    forms = json.loads(input_export.read_text(encoding='utf-8'))
    tables = json.loads(table_export.read_text(encoding='utf-8'))
    device1 = forms['aliasesInfo']['datasourceAliases']['0']['filter']['singleEntity']['id']

    def bind(doc, field):
        alias_id = str(uuid.uuid4())
        doc['widget']['id'] = str(uuid.uuid4())
        doc['widget']['config']['datasources'][0]['entityAliasId'] = alias_id
        doc['aliasesInfo'] = {'datasourceAliases': {'0': {
            'alias': f'Cấu hình Field {field}',
            'filter': {'type': 'singleEntity', 'resolveMultiple': False,
                       'singleEntity': {'entityType': 'DEVICE', 'id': device1 if field == 1 else None}}
        }}, 'targetDeviceAlias': None}
        doc['widget']['config']['actions'] = {}
        doc['widget']['config']['useDashboardTimewindow'] = False
        doc['widget']['config']['displayTimewindow'] = False

    def save(name, doc):
        (output / name).write_text(json.dumps(doc, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    for field in (1, 2):
        doc = copy.deepcopy(forms)
        bind(doc, field)
        cfg = doc['widget']['config']
        cfg['title'] = cfg['settings']['widgetTitle'] = f'Cấu hình tưới – Field {field}'
        cfg['noDataDisplayMessage'] = 'Chưa có Shared attributes; kiểm tra thiết bị và cấu hình ban đầu.'
        cfg['settings']['updateAllValues'] = True
        if field == 2:
            save('field_2_config_6_keys.json', doc)
        for name, label, units, kind, minimum, step in [
            ('maxWaterPerCycle', 'Nước tối đa mỗi lần tưới', 'L', 'double', 0.001, 0.001),
            ('maxWaterPerDay', 'Nước tối đa mỗi ngày', 'L', 'double', 0.001, 0.001),
            ('maxDurationSec', 'Thời gian tưới tối đa', 's', 'integer', 1, 1),
        ]:
            key = copy.deepcopy(cfg['datasources'][0]['dataKeys'][1])
            key.update(name=name, label=label, units=units)
            key.pop('_hash', None)
            key['settings'].update(dataKeyValueType=kind, minValue=minimum, maxValue=None, step=step)
            cfg['datasources'][0]['dataKeys'].append(key)
        cfg['titleTooltip'] = 'Lưu trên CoreIoT chưa xác nhận Gateway áp dụng. Xem bảng phản hồi; hạn mức ngày phải >= mỗi lần.'
        doc['widget']['sizeY'] = 6
        doc['originalSize']['sizeY'] = 10
        save(f'field_{field}_config_9_keys.json', doc)

        status = copy.deepcopy(tables)
        bind(status, field)
        scfg = status['widget']['config']
        scfg['title'] = f'Phản hồi cấu hình – Field {field}'
        scfg['titleTooltip'] = 'Phản hồi gần nhất từ Gateway, không phải trạng thái kết nối. So sánh yêu cầu và hiệu lực; giờ áp dụng phải mới sau khi lưu.'
        scfg['settings'].update(entitiesTitle=f'Field {field}', defaultSortOrder='name',
            displayPagination=False, enableSearch=False, enableSelectColumnDisplay=True)
        keys = []
        definitions = [('name', 'entityField', 'Thiết bị'),
            ('fieldConfigStatus', 'timeseries', 'Phản hồi gần nhất'),
            ('fieldConfigReason', 'timeseries', 'Lý do'),
            ('fieldConfigAppliedAt', 'timeseries', 'Lần áp dụng thành công'),
            ('targetMoisture', 'attribute', 'Mục tiêu yêu cầu (%)'),
            ('targetMoisture', 'timeseries', 'Mục tiêu hiệu lực (%)'),
            ('maxWaterPerCycle', 'timeseries', 'Hạn mức/lần (L)'),
            ('maxWaterPerDay', 'timeseries', 'Hạn mức/ngày (L)'),
            ('maxDurationSec', 'timeseries', 'Thời gian tối đa (s)'),
            ('fieldConfigEffective', 'timeseries', 'Toàn bộ cấu hình hiệu lực')]
        for name, kind, label in definitions:
            key = copy.deepcopy(tables['widget']['config']['datasources'][0]['dataKeys'][0])
            key.update(name=name, type=kind, label=label, units=None)
            key.pop('_hash', None)
            key['settings'].update(columnWidth='', useCellStyleFunction=False, useCellContentFunction=False)
            if name == 'fieldConfigStatus':
                key['settings'].update(useCellContentFunction=True, cellContentFunction="return ({APPLIED:'Đã áp dụng',REJECTED:'Bị từ chối',WAITING:'Đang chờ cấu hình',LOCAL_ONLY:'Dùng scenario'})[value] || 'Chưa có phản hồi';")
            if name == 'fieldConfigAppliedAt':
                key['settings'].update(useCellContentFunction=True, cellContentFunction="return Number(value) > 0 ? new Date(Number(value)).toLocaleString('vi-VN') : 'Chưa áp dụng';")
            keys.append(key)
        scfg['datasources'][0]['dataKeys'] = keys
        status['originalSize'] = {'sizeX': 24, 'sizeY': 4}
        save(f'field_{field}_config_status.json', status)

    # Preserve two explicit device aliases: importing must resolve Field 2,
    # never silently point both groups at the known Field 1 device ID.
    for suffix, filename, title in [
        ('config_9_keys', 'two_fields_config_9_keys.json', 'Cấu hình tưới – Hai Field'),
        ('config_status', 'two_fields_config_status.json', 'Phản hồi cấu hình – Hai Field'),
    ]:
        merged = json.loads((output / f'field_1_{suffix}.json').read_text(encoding='utf-8'))
        second = json.loads((output / f'field_2_{suffix}.json').read_text(encoding='utf-8'))
        merged['widget']['id'] = str(uuid.uuid4())
        cfg = merged['widget']['config']
        cfg['datasources'].append(second['widget']['config']['datasources'][0])
        merged['aliasesInfo']['datasourceAliases']['1'] = second['aliasesInfo']['datasourceAliases']['0']
        cfg['title'] = title
        if suffix == 'config_9_keys':
            cfg['settings'].update(widgetTitle=title, showGroupTitle=True,
                groupTitle='${entityName}', saveButtonLabel='Lưu cấu hình hai Field')
            cfg['titleTooltip'] = 'Hai Field được kiểm tra và áp dụng riêng. Có thể một Field được áp dụng, Field còn lại bị từ chối; xem bảng phản hồi.'
            merged['originalSize'] = {'sizeX': 24, 'sizeY': 16}
            merged['widget']['sizeY'] = 12
        else:
            cfg['settings']['entitiesTitle'] = 'Kết quả Gateway áp dụng'
            cfg['widgetCss'] = ''
            cfg['noDataDisplayMessage'] = 'Chưa có phản hồi Gateway; kiểm tra hai alias thiết bị.'
            for ds in cfg['datasources']:
                # Keep the JSON available through the column picker without
                # making the default table excessively wide.
                for key in ds['dataKeys']:
                    if key['name'] == 'fieldConfigEffective':
                        key['settings']['defaultColumnVisibility'] = 'hidden'
        save(filename, merged)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-export', type=Path, required=True)
    parser.add_argument('--table-export', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT/'deliverables/coreiot/irrigation_widgets')
    args = parser.parse_args()
    build(args.input_export, args.table_export, args.output)
