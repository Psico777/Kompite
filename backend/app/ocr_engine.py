"""
=============================================================================
KOMPITE - Motor de OCR y Análisis Forense de Comprobantes
=============================================================================
Sistema de Inteligencia de Entrada para validación automática de capturas
de Yape, Plin e Interoperabilidad Bancaria.

CAPACIDADES:
1. Detección de patrones visuales (colores, layouts, tipografía)
2. Extracción de datos críticos (monto, fecha, número de operación)
3. Validación de frescura temporal (máximo 24 horas)
4. Detección de duplicados (transaction_reference existente)
5. Análisis forense de metadatos (detección de edición)
6. Coherencia tipográfica (alineación, resolución de dígitos)

NOTA: Esta es una implementación Mock que simula OCR. En producción,
integrar con servicios como Google Vision, AWS Textract o Tesseract.
=============================================================================
"""

import hashlib
import io
import re
import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from enum import Enum
from typing import Optional, List, Dict, Any, Tuple
from uuid import UUID
import base64


# =============================================================================
# ENUMERACIONES
# =============================================================================

class PaymentProvider(str, Enum):
    """Proveedores de pago detectables."""
    YAPE = "YAPE"
    PLIN = "PLIN"
    YAPE_TO_PLIN = "YAPE_TO_PLIN"  # Interoperabilidad
    PLIN_TO_YAPE = "PLIN_TO_YAPE"
    BCP = "BCP"
    BBVA = "BBVA"
    INTERBANK = "INTERBANK"
    SCOTIABANK = "SCOTIABANK"
    UNKNOWN = "UNKNOWN"


class AnalysisVerdict(str, Enum):
    """Veredicto del análisis."""
    APPROVED = "APPROVED"           # Pasa todas las validaciones
    NEEDS_REVIEW = "NEEDS_REVIEW"   # Requiere revisión manual
    REJECTED = "REJECTED"           # Rechazado automáticamente
    SUSPICIOUS = "SUSPICIOUS"       # Actividad sospechosa detectada


class ForensicFlag(str, Enum):
    """Banderas forenses detectadas."""
    EDITED_METADATA = "EDITED_METADATA"         # Software de edición detectado
    TYPOGRAPHY_MISMATCH = "TYPOGRAPHY_MISMATCH" # Inconsistencia tipográfica
    EXPIRED_RECEIPT = "EXPIRED_RECEIPT"         # Comprobante > 24 horas
    DUPLICATE_REFERENCE = "DUPLICATE_REFERENCE" # Referencia ya usada
    LOW_RESOLUTION = "LOW_RESOLUTION"           # Resolución sospechosamente baja
    SCREENSHOT_CROPPED = "SCREENSHOT_CROPPED"   # Captura recortada
    COLOR_ANOMALY = "COLOR_ANOMALY"             # Colores manipulados
    FONT_INCONSISTENCY = "FONT_INCONSISTENCY"   # Fuentes no coinciden
    TIMESTAMP_MISMATCH = "TIMESTAMP_MISMATCH"   # Hora de archivo vs hora en imagen


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class ExtractedData:
    """Datos extraídos del comprobante."""
    amount: Optional[Decimal] = None
    currency: str = "PEN"
    transaction_reference: Optional[str] = None
    transaction_date: Optional[datetime] = None
    sender_name: Optional[str] = None
    sender_phone: Optional[str] = None
    receiver_name: Optional[str] = None
    receiver_phone: Optional[str] = None
    provider: PaymentProvider = PaymentProvider.UNKNOWN
    raw_text: str = ""
    confidence_scores: Dict[str, float] = field(default_factory=dict)


@dataclass
class ForensicAnalysis:
    """Resultado del análisis forense."""
    is_screenshot: bool = True
    is_edited: bool = False
    editing_software: Optional[str] = None
    creation_date: Optional[datetime] = None
    modification_date: Optional[datetime] = None
    device_model: Optional[str] = None
    resolution: Tuple[int, int] = (0, 0)
    color_profile: Optional[str] = None
    has_exif: bool = False
    flags: List[ForensicFlag] = field(default_factory=list)
    metadata_raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OCRResult:
    """Resultado completo del análisis OCR."""
    success: bool
    verdict: AnalysisVerdict
    extracted_data: ExtractedData
    forensic_analysis: ForensicAnalysis
    confidence: float  # 0.0 - 1.0
    processing_time_ms: float
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convierte a diccionario para respuesta JSON."""
        return {
            "success": self.success,
            "verdict": self.verdict.value,
            "confidence": round(self.confidence, 2),
            "processing_time_ms": round(self.processing_time_ms, 2),
            "extracted_data": {
                "amount": str(self.extracted_data.amount) if self.extracted_data.amount else None,
                "currency": self.extracted_data.currency,
                "transaction_reference": self.extracted_data.transaction_reference,
                "transaction_date": self.extracted_data.transaction_date.isoformat() if self.extracted_data.transaction_date else None,
                "provider": self.extracted_data.provider.value,
                "sender_name": self.extracted_data.sender_name,
                "receiver_name": self.extracted_data.receiver_name,
                "confidence_scores": self.extracted_data.confidence_scores,
            },
            "forensic_analysis": {
                "is_screenshot": self.forensic_analysis.is_screenshot,
                "is_edited": self.forensic_analysis.is_edited,
                "editing_software": self.forensic_analysis.editing_software,
                "resolution": list(self.forensic_analysis.resolution),
                "flags": [f.value for f in self.forensic_analysis.flags],
            },
            "warnings": self.warnings,
            "errors": self.errors,
            "recommendations": self.recommendations,
        }


# =============================================================================
# PATRONES DE DETECCIÓN
# =============================================================================

class ProviderPatterns:
    """Patrones de detección para cada proveedor de pagos."""
    
    # Colores predominantes (RGB aproximados)
    COLORS = {
        PaymentProvider.YAPE: {
            "primary": (128, 0, 128),      # Morado Yape
            "secondary": (255, 255, 255),  # Blanco
            "tolerance": 40,
        },
        PaymentProvider.PLIN: {
            "primary": (0, 191, 165),      # Verde/Turquesa Plin
            "secondary": (255, 255, 255),
            "tolerance": 40,
        },
        PaymentProvider.BCP: {
            "primary": (255, 102, 0),      # Naranja BCP
            "secondary": (0, 51, 102),     # Azul oscuro
            "tolerance": 35,
        },
        PaymentProvider.BBVA: {
            "primary": (0, 68, 129),       # Azul BBVA
            "secondary": (255, 255, 255),
            "tolerance": 35,
        },
    }
    
    # Textos característicos
    TEXT_PATTERNS = {
        PaymentProvider.YAPE: [
            r"¡?yapeaste!?",
            r"yapeo\s+exitoso",
            r"operación\s+exitosa",
            r"yape",
        ],
        PaymentProvider.PLIN: [
            r"plin",
            r"transferencia\s+exitosa",
            r"¡listo!",
        ],
        PaymentProvider.BCP: [
            r"bcp",
            r"banco\s+de\s+cr[eé]dito",
            r"v[ií]a\s+bcp",
            r"transferencia\s+interbancaria",
        ],
        PaymentProvider.BBVA: [
            r"bbva",
            r"bbva\s+continental",
            r"bbva\s+per[uú]",
        ],
    }
    
    # Patrones de número de operación
    OPERATION_PATTERNS = {
        PaymentProvider.YAPE: r"(?:n[°º]?\s*(?:de\s+)?operaci[oó]n|op\.?)\s*[:\-]?\s*(\d{8,12})",
        PaymentProvider.PLIN: r"(?:n[°º]?\s*(?:de\s+)?operaci[oó]n|c[oó]digo)\s*[:\-]?\s*(\d{8,12})",
        PaymentProvider.BCP: r"(?:n[°º]?\s*(?:de\s+)?operaci[oó]n|nro\.?\s*op\.?)\s*[:\-]?\s*(\d{8,15})",
        PaymentProvider.BBVA: r"(?:n[°º]?\s*(?:de\s+)?operaci[oó]n|referencia)\s*[:\-]?\s*(\d{8,15})",
    }
    
    # Patrones de monto
    AMOUNT_PATTERNS = [
        r"s/\.?\s*([\d,]+\.?\d{0,2})",           # S/ 50.00
        r"([\d,]+\.?\d{0,2})\s*(?:soles|pen)",   # 50.00 soles
        r"monto[:\s]+([\d,]+\.?\d{0,2})",        # Monto: 50.00
        r"total[:\s]+([\d,]+\.?\d{0,2})",        # Total: 50.00
    ]
    
    # Patrones de fecha/hora
    DATE_PATTERNS = [
        r"(\d{1,2})\s+(?:de\s+)?(\w+)\s+(?:de\s+)?(\d{4})\s+(\d{1,2}):(\d{2})",  # 15 de enero de 2026 14:30
        r"(\d{1,2})/(\d{1,2})/(\d{2,4})\s+(\d{1,2}):(\d{2})",                     # 15/01/2026 14:30
        r"(\d{1,2})-(\d{1,2})-(\d{2,4})\s+(\d{1,2}):(\d{2})",                     # 15-01-2026 14:30
    ]


# =============================================================================
# MOTOR DE OCR MOCK
# =============================================================================

class OCREngine:
    """
    Motor de OCR y análisis forense para comprobantes de pago.
    
    NOTA: Esta es una implementación Mock que simula el procesamiento.
    En producción, integrar con:
    - Google Cloud Vision API
    - AWS Textract
    - Azure Computer Vision
    - Tesseract OCR (local)
    """
    
    # Software de edición conocidos (detectables en metadatos)
    EDITING_SOFTWARE = [
        "photoshop", "gimp", "canva", "pixlr", "snapseed",
        "lightroom", "illustrator", "affinity", "paint.net",
        "photopea", "polarr", "vsco", "prisma"
    ]
    
    # Marcadores de capturas legítimas
    SCREENSHOT_MARKERS = [
        "screenshot", "captura", "screen", "snap",
    ]
    
    def __init__(self, max_age_hours: int = 24):
        """
        Inicializa el motor OCR.
        
        Args:
            max_age_hours: Antigüedad máxima permitida para comprobantes
        """
        self.max_age = timedelta(hours=max_age_hours)
        self.patterns = ProviderPatterns()
        
        # Cache de referencias procesadas (en producción usar Redis)
        self._processed_references: Dict[str, datetime] = {}
    
    async def analyze_image(
        self,
        image_data: bytes,
        filename: Optional[str] = None,
        user_id: Optional[UUID] = None,
        check_duplicates: bool = True
    ) -> OCRResult:
        """
        Analiza una imagen de comprobante de pago.
        
        Args:
            image_data: Bytes de la imagen
            filename: Nombre original del archivo
            user_id: ID del usuario que sube el comprobante
            check_duplicates: Si verificar referencias duplicadas
            
        Returns:
            OCRResult con el análisis completo
        """
        import time
        start_time = time.perf_counter()
        
        warnings = []
        errors = []
        recommendations = []
        flags = []
        
        # 1. Análisis forense de metadatos
        forensic = await self._analyze_forensics(image_data, filename)
        flags.extend(forensic.flags)
        
        # 2. Detección del proveedor de pagos
        provider = await self._detect_provider(image_data)
        
        # 3. Extracción de datos (Mock)
        extracted = await self._extract_data(image_data, provider)
        
        # 4. Validaciones
        
        # 4.1 Verificar frescura del comprobante
        if extracted.transaction_date:
            age = datetime.now(timezone.utc) - extracted.transaction_date
            if age > self.max_age:
                flags.append(ForensicFlag.EXPIRED_RECEIPT)
                errors.append(
                    f"Comprobante expirado. Antigüedad: {age.total_seconds() / 3600:.1f} horas. "
                    f"Máximo permitido: {self.max_age.total_seconds() / 3600:.0f} horas."
                )
        
        # 4.2 Verificar duplicados
        if check_duplicates and extracted.transaction_reference:
            if await self._is_duplicate_reference(extracted.transaction_reference):
                flags.append(ForensicFlag.DUPLICATE_REFERENCE)
                errors.append(
                    f"Referencia {extracted.transaction_reference} ya fue utilizada anteriormente."
                )
        
        # 4.3 Verificar edición
        if forensic.is_edited:
            flags.append(ForensicFlag.EDITED_METADATA)
            warnings.append(
                f"Imagen procesada con software de edición: {forensic.editing_software}"
            )
        
        # 4.4 Verificar resolución
        if forensic.resolution[0] < 300 or forensic.resolution[1] < 400:
            flags.append(ForensicFlag.LOW_RESOLUTION)
            warnings.append("Resolución de imagen muy baja. Puede dificultar la verificación.")
        
        # 5. Calcular confianza y veredicto
        confidence = self._calculate_confidence(extracted, forensic, flags)
        verdict = self._determine_verdict(confidence, flags, errors)
        
        # 6. Generar recomendaciones
        if verdict == AnalysisVerdict.NEEDS_REVIEW:
            recommendations.append("Por favor, asegúrese de que el Nro. de Operación sea legible.")
            if provider == PaymentProvider.UNKNOWN:
                recommendations.append("No se pudo identificar el proveedor. Verifique que sea una captura válida.")
        
        if verdict == AnalysisVerdict.REJECTED:
            if ForensicFlag.EXPIRED_RECEIPT in flags:
                recommendations.append("Suba un comprobante de las últimas 24 horas.")
            if ForensicFlag.DUPLICATE_REFERENCE in flags:
                recommendations.append("Esta referencia ya fue usada. Contacte a soporte si cree que es un error.")
        
        processing_time = (time.perf_counter() - start_time) * 1000
        
        return OCRResult(
            success=verdict in [AnalysisVerdict.APPROVED, AnalysisVerdict.NEEDS_REVIEW],
            verdict=verdict,
            extracted_data=extracted,
            forensic_analysis=forensic,
            confidence=confidence,
            processing_time_ms=processing_time,
            warnings=warnings,
            errors=errors,
            recommendations=recommendations,
        )
    
    async def _analyze_forensics(
        self,
        image_data: bytes,
        filename: Optional[str]
    ) -> ForensicAnalysis:
        """
        Analiza los metadatos forenses de la imagen.
        
        Detecta:
        - Software de edición
        - Timestamps de creación/modificación
        - Modelo de dispositivo
        - Si es captura de pantalla legítima
        """
        forensic = ForensicAnalysis()
        flags = []
        
        # Detectar formato de imagen
        image_format = self._detect_image_format(image_data)
        
        # Extraer dimensiones
        forensic.resolution = self._extract_dimensions(image_data, image_format)
        
        # Analizar metadatos según formato
        if image_format == "PNG":
            metadata = self._parse_png_metadata(image_data)
        elif image_format in ["JPEG", "JPG"]:
            metadata = self._parse_jpeg_metadata(image_data)
        else:
            metadata = {}
        
        forensic.metadata_raw = metadata
        forensic.has_exif = bool(metadata)
        
        # Detectar software de edición
        software = metadata.get("software", "").lower()
        for editor in self.EDITING_SOFTWARE:
            if editor in software:
                forensic.is_edited = True
                forensic.editing_software = software
                flags.append(ForensicFlag.EDITED_METADATA)
                break
        
        # Detectar si es captura de pantalla legítima
        forensic.is_screenshot = self._is_legitimate_screenshot(filename, metadata)
        
        # Verificar timestamp consistency
        if metadata.get("creation_date") and metadata.get("modification_date"):
            creation = metadata["creation_date"]
            modification = metadata["modification_date"]
            if modification < creation:
                flags.append(ForensicFlag.TIMESTAMP_MISMATCH)
        
        # Extraer modelo de dispositivo
        forensic.device_model = metadata.get("device_model")
        
        forensic.flags = flags
        return forensic
    
    async def _detect_provider(self, image_data: bytes) -> PaymentProvider:
        """
        Detecta el proveedor de pagos basándose en patrones visuales.
        
        En producción: usar OCR real para extraer texto y análisis de color.
        Mock: simula detección basada en hash de imagen.
        """
        # Mock: usar hash para simular detección determinística
        image_hash = hashlib.md5(image_data).hexdigest()
        hash_num = int(image_hash[:8], 16)
        
        # Simular detección basada en hash (para desarrollo)
        providers = [
            PaymentProvider.YAPE,
            PaymentProvider.PLIN,
            PaymentProvider.YAPE_TO_PLIN,
            PaymentProvider.BCP,
            PaymentProvider.BBVA,
        ]
        
        # 70% chance de detectar correctamente, 30% UNKNOWN
        if hash_num % 10 < 7:
            return providers[hash_num % len(providers)]
        return PaymentProvider.UNKNOWN
    
    async def _extract_data(
        self,
        image_data: bytes,
        provider: PaymentProvider
    ) -> ExtractedData:
        """
        Extrae datos estructurados del comprobante.
        
        En producción: usar OCR real para extraer texto y parsearlo.
        Mock: genera datos simulados basados en el hash de la imagen.
        """
        image_hash = hashlib.md5(image_data).hexdigest()
        hash_num = int(image_hash[:8], 16)
        
        # Generar datos mock determinísticos
        amount = Decimal(str(10 + (hash_num % 990)))  # 10 - 1000
        reference = f"{provider.value[:4].upper()}-{hash_num % 100000000:08d}"
        
        # Fecha: entre ahora y hace 48 horas (para probar validación de frescura)
        hours_ago = hash_num % 72  # 0-72 horas
        tx_date = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
        
        # Confidence scores por campo
        confidence_scores = {
            "amount": 0.85 + (hash_num % 15) / 100,      # 0.85 - 1.00
            "reference": 0.75 + (hash_num % 25) / 100,   # 0.75 - 1.00
            "date": 0.80 + (hash_num % 20) / 100,        # 0.80 - 1.00
            "provider": 0.90 if provider != PaymentProvider.UNKNOWN else 0.30,
        }
        
        return ExtractedData(
            amount=amount,
            currency="PEN",
            transaction_reference=reference,
            transaction_date=tx_date,
            sender_name="Usuario Kompite",
            provider=provider,
            confidence_scores=confidence_scores,
        )
    
    async def _is_duplicate_reference(self, reference: str) -> bool:
        """
        Verifica si la referencia ya fue usada.
        
        En producción: verificar contra la tabla Deposit en la BD.
        Mock: usa cache en memoria.
        """
        # En producción:
        # result = await db.execute(
        #     select(Deposit).where(Deposit.transaction_reference == reference)
        # )
        # return result.scalar_one_or_none() is not None
        
        is_duplicate = reference in self._processed_references
        if not is_duplicate:
            self._processed_references[reference] = datetime.now(timezone.utc)
        return is_duplicate
    
    def _calculate_confidence(
        self,
        extracted: ExtractedData,
        forensic: ForensicAnalysis,
        flags: List[ForensicFlag]
    ) -> float:
        """Calcula la confianza general del análisis."""
        base_confidence = 0.5
        
        # Sumar por datos extraídos
        if extracted.amount:
            base_confidence += 0.15
        if extracted.transaction_reference:
            base_confidence += 0.15
        if extracted.transaction_date:
            base_confidence += 0.10
        if extracted.provider != PaymentProvider.UNKNOWN:
            base_confidence += 0.10
        
        # Penalizar por flags
        penalty_map = {
            ForensicFlag.EDITED_METADATA: 0.25,
            ForensicFlag.DUPLICATE_REFERENCE: 0.40,
            ForensicFlag.EXPIRED_RECEIPT: 0.30,
            ForensicFlag.LOW_RESOLUTION: 0.10,
            ForensicFlag.TYPOGRAPHY_MISMATCH: 0.20,
            ForensicFlag.TIMESTAMP_MISMATCH: 0.15,
        }
        
        for flag in flags:
            base_confidence -= penalty_map.get(flag, 0.05)
        
        return max(0.0, min(1.0, base_confidence))
    
    def _determine_verdict(
        self,
        confidence: float,
        flags: List[ForensicFlag],
        errors: List[str]
    ) -> AnalysisVerdict:
        """Determina el veredicto final del análisis."""
        
        # Rechazos automáticos
        critical_flags = [
            ForensicFlag.DUPLICATE_REFERENCE,
            ForensicFlag.EXPIRED_RECEIPT,
        ]
        
        for flag in critical_flags:
            if flag in flags:
                return AnalysisVerdict.REJECTED
        
        # Sospechoso si tiene edición detectada
        if ForensicFlag.EDITED_METADATA in flags:
            return AnalysisVerdict.SUSPICIOUS
        
        # Por confianza
        if confidence >= 0.75:
            return AnalysisVerdict.APPROVED
        elif confidence >= 0.50:
            return AnalysisVerdict.NEEDS_REVIEW
        else:
            return AnalysisVerdict.REJECTED
    
    # =========================================================================
    # UTILIDADES DE PARSING DE IMAGEN
    # =========================================================================
    
    def _detect_image_format(self, data: bytes) -> str:
        """Detecta el formato de imagen por magic bytes."""
        if data[:8] == b'\x89PNG\r\n\x1a\n':
            return "PNG"
        elif data[:2] == b'\xff\xd8':
            return "JPEG"
        elif data[:6] in (b'GIF87a', b'GIF89a'):
            return "GIF"
        elif data[:4] == b'RIFF' and data[8:12] == b'WEBP':
            return "WEBP"
        return "UNKNOWN"
    
    def _extract_dimensions(self, data: bytes, format: str) -> Tuple[int, int]:
        """Extrae las dimensiones de la imagen."""
        try:
            if format == "PNG":
                # PNG: dimensiones en bytes 16-23
                width = struct.unpack('>I', data[16:20])[0]
                height = struct.unpack('>I', data[20:24])[0]
                return (width, height)
            elif format == "JPEG":
                # JPEG: buscar marcador SOF
                return self._extract_jpeg_dimensions(data)
        except Exception:
            pass
        return (0, 0)
    
    def _extract_jpeg_dimensions(self, data: bytes) -> Tuple[int, int]:
        """Extrae dimensiones de un JPEG."""
        i = 2
        while i < len(data) - 8:
            if data[i] != 0xFF:
                break
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2):  # SOF markers
                height = struct.unpack('>H', data[i + 5:i + 7])[0]
                width = struct.unpack('>H', data[i + 7:i + 9])[0]
                return (width, height)
            length = struct.unpack('>H', data[i + 2:i + 4])[0]
            i += 2 + length
        return (0, 0)
    
    def _parse_png_metadata(self, data: bytes) -> Dict[str, Any]:
        """Parsea metadatos de un PNG (chunks tEXt, iTXt)."""
        metadata = {}
        i = 8  # Skip PNG signature
        
        while i < len(data) - 12:
            try:
                length = struct.unpack('>I', data[i:i + 4])[0]
                chunk_type = data[i + 4:i + 8].decode('ascii')
                chunk_data = data[i + 8:i + 8 + length]
                
                if chunk_type == 'tEXt':
                    # Null-separated key-value
                    null_idx = chunk_data.find(b'\x00')
                    if null_idx > 0:
                        key = chunk_data[:null_idx].decode('latin-1')
                        value = chunk_data[null_idx + 1:].decode('latin-1')
                        metadata[key.lower()] = value
                        
                        if 'software' in key.lower():
                            metadata['software'] = value
                
                i += 12 + length
            except Exception:
                break
        
        return metadata
    
    def _parse_jpeg_metadata(self, data: bytes) -> Dict[str, Any]:
        """Parsea metadatos EXIF de un JPEG."""
        metadata = {}
        
        # Buscar segmento APP1 (EXIF)
        i = 2
        while i < len(data) - 4:
            if data[i] != 0xFF:
                break
            marker = data[i + 1]
            
            if marker == 0xE1:  # APP1
                length = struct.unpack('>H', data[i + 2:i + 4])[0]
                segment = data[i + 4:i + 2 + length]
                
                if segment[:4] == b'Exif':
                    # Simplificado: marcar que tiene EXIF
                    metadata['has_exif'] = True
                    # En producción: parsear EXIF completo
                
            elif marker in (0xD8, 0xD9, 0x00):
                break
            else:
                length = struct.unpack('>H', data[i + 2:i + 4])[0]
                i += 2 + length
                continue
            
            i += 2
        
        return metadata
    
    def _is_legitimate_screenshot(
        self,
        filename: Optional[str],
        metadata: Dict[str, Any]
    ) -> bool:
        """Determina si la imagen parece ser una captura de pantalla legítima."""
        if filename:
            filename_lower = filename.lower()
            for marker in self.SCREENSHOT_MARKERS:
                if marker in filename_lower:
                    return True
            # Patrones típicos de capturas de Android/iOS
            if re.match(r'screenshot_\d+', filename_lower):
                return True
            if re.match(r'img_\d{8}_\d{6}', filename_lower):
                return True
        
        return True  # Por defecto asumir que es válida


# =============================================================================
# INSTANCIA GLOBAL
# =============================================================================

ocr_engine = OCREngine(max_age_hours=24)


# =============================================================================
# FUNCIONES DE UTILIDAD PARA INTEGRACIÓN
# =============================================================================

async def analyze_deposit_evidence(
    image_data: bytes,
    filename: str,
    user_id: UUID
) -> OCRResult:
    """
    Función de conveniencia para analizar evidencia de depósito.
    Integra con el sistema de seguridad LK-SHIELD si detecta actividad sospechosa.
    """
    result = await ocr_engine.analyze_image(
        image_data=image_data,
        filename=filename,
        user_id=user_id,
        check_duplicates=True
    )
    
    # Si es sospechoso o tiene referencia duplicada, marcar en LK-SHIELD
    if result.verdict == AnalysisVerdict.SUSPICIOUS:
        # TODO: Integrar con LK-SHIELD
        # await lk_shield.flag_suspicious_activity(
        #     user_id=user_id,
        #     reason="OCR detectó evidencia manipulada",
        #     flags=result.forensic_analysis.flags
        # )
        pass
    
    return result


def format_terminal_output(result: OCRResult) -> str:
    """
    Formatea el resultado del OCR como salida de terminal para el Admin Panel.
    Estilo Cyber-Luxury para auditoría.
    """
    lines = [
        "╔══════════════════════════════════════════════════════════════╗",
        "║  KOMPITE FORENSIC SCANNER v1.0                              ║",
        "╠══════════════════════════════════════════════════════════════╣",
    ]
    
    # Veredicto con color
    verdict_icons = {
        AnalysisVerdict.APPROVED: "✓ APPROVED",
        AnalysisVerdict.NEEDS_REVIEW: "⚠ NEEDS REVIEW",
        AnalysisVerdict.REJECTED: "✗ REJECTED",
        AnalysisVerdict.SUSPICIOUS: "🚨 SUSPICIOUS",
    }
    
    lines.append(f"║  VERDICT: {verdict_icons[result.verdict]:<50} ║")
    lines.append(f"║  CONFIDENCE: {result.confidence:.1%:<47} ║")
    lines.append(f"║  PROCESSING: {result.processing_time_ms:.2f}ms{' ' * 44}║")
    lines.append("╠══════════════════════════════════════════════════════════════╣")
    
    # Datos extraídos
    lines.append("║  [EXTRACTED DATA]                                            ║")
    ed = result.extracted_data
    lines.append(f"║    Provider: {ed.provider.value:<48} ║")
    lines.append(f"║    Amount: S/ {str(ed.amount):<46} ║")
    lines.append(f"║    Reference: {ed.transaction_reference or 'N/A':<47} ║")
    if ed.transaction_date:
        lines.append(f"║    Date: {ed.transaction_date.strftime('%Y-%m-%d %H:%M:%S'):<52} ║")
    
    lines.append("╠══════════════════════════════════════════════════════════════╣")
    
    # Análisis forense
    lines.append("║  [FORENSIC ANALYSIS]                                         ║")
    fa = result.forensic_analysis
    lines.append(f"║    Resolution: {fa.resolution[0]}x{fa.resolution[1]:<44} ║")
    lines.append(f"║    Screenshot: {'Yes' if fa.is_screenshot else 'No':<46} ║")
    lines.append(f"║    Edited: {'Yes' if fa.is_edited else 'No':<50} ║")
    
    if fa.flags:
        lines.append("║    Flags:                                                    ║")
        for flag in fa.flags:
            lines.append(f"║      → {flag.value:<53} ║")
    
    lines.append("╠══════════════════════════════════════════════════════════════╣")
    
    # Warnings y errores
    if result.warnings:
        lines.append("║  [WARNINGS]                                                  ║")
        for warning in result.warnings:
            # Truncar si es muy largo
            w = warning[:55] + "..." if len(warning) > 55 else warning
            lines.append(f"║    ⚠ {w:<55} ║")
    
    if result.errors:
        lines.append("║  [ERRORS]                                                    ║")
        for error in result.errors:
            e = error[:55] + "..." if len(error) > 55 else error
            lines.append(f"║    ✗ {e:<55} ║")
    
    lines.append("╚══════════════════════════════════════════════════════════════╝")
    
    return "\n".join(lines)
