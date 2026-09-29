"""
Дефолтный справочник типов нарушений (п.6 ТЗ). Загружается в БД при первом
старте (таблица violationtype), дальше редактируется администратором через API
— модуль AI обращается к нарушениям по устойчивому полю `code`, поэтому
переименование name_ru администратором не ломает связь с AI-модулем.
"""

DEFAULT_VIOLATION_TYPES = [
    {
        "code": "incorrect_positioning",
        "name_ru": "Неправильное позиционирование",
        "category": "positioning",
        "description": "Пациент/анатомическая область смещены или развёрнуты относительно "
                        "стандартного положения для данного вида денситометрии.",
        "default_severity": "high",
        "is_system": True,
    },
    {
        "code": "incomplete_field_of_view",
        "name_ru": "Неполная/неправильная область исследования",
        "category": "field_of_view",
        "description": "Анатомическая область исследования не полностью попадает в поле "
                        "снимка (обрезаны края) либо захвачена не та область.",
        "default_severity": "high",
        "is_system": True,
    },
    {
        "code": "artifact",
        "name_ru": "Артефакты на изображении",
        "category": "artifact",
        "description": "Посторонние объекты/артефакты в поле снимка (металл, элементы "
                        "одежды, аппаратные помехи и т.п.).",
        "default_severity": "medium",
        "is_system": True,
    },
    {
        "code": "low_image_quality",
        "name_ru": "Низкое качество изображения",
        "category": "image_quality",
        "description": "Смазанность (движение пациента), недостаточная/избыточная "
                        "экспозиция, низкий контраст.",
        "default_severity": "medium",
        "is_system": True,
    },
    {
        "code": "roi_segmentation_error",
        "name_ru": "Ошибки ROI/сегментации",
        "category": "roi_segmentation",
        "description": "Разметка анатомических структур (ROI) не соответствует "
                        "эталонной/ожидаемой геометрии.",
        "default_severity": "high",
        "is_system": True,
    },
    {
        "code": "dicom_technical_error",
        "name_ru": "Технические ошибки DICOM",
        "category": "dicom_technical",
        "description": "Отсутствуют или некорректны обязательные DICOM-теги, "
                        "несоответствие размеров PixelData заявленным Rows/Columns и т.п.",
        "default_severity": "critical",
        "is_system": True,
    },
    {
        "code": "phi_not_removed",
        "name_ru": "Не выполнено обезличивание данных",
        "category": "phi",
        "description": "В DICOM-файле обнаружены персональные данные пациента "
                        "(ФИО, дата рождения и т.п.), которые должны быть удалены до анализа.",
        "default_severity": "critical",
        "is_system": True,
    },
]
