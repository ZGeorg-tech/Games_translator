use std::sync::{Arc, Mutex};

use pyo3::prelude::*;
use windows_capture::{
    capture::{CaptureControl, Context, GraphicsCaptureApiHandler},
    frame::Frame,
    graphics_capture_api::InternalCaptureControl,
    settings::{
        ColorFormat, CursorCaptureSettings, DirtyRegionSettings, DrawBorderSettings,
        MinimumUpdateIntervalSettings, SecondaryWindowSettings, Settings,
    },
    window::Window,
};

// -----------------------------------------------------------------------------
// Rust-модуль аппаратного захвата кадра для Python.
//
// ВАЖНО:
// Windows Graphics Capture может отдавать кадр не как плотный массив
// width * height * 4, а с выравниванием строк. Это называется row_pitch.
//
// Если игнорировать row_pitch и считать начало строки так:
//     (y * frame_width + x) * 4
// то при наличии padding каждая следующая строка будет читаться со смещением.
// В debug_capture это выглядит как черное поле, диагональные точки/полосы,
// «лесенка» или другой геометрический мусор.
//
// Поэтому ниже мы считаем начало строки так:
//     y * row_pitch + x * 4
// -----------------------------------------------------------------------------

#[derive(Clone)]
struct CaptureState {
    // Последний готовый кроп кадра, который забирает Python.
    data: Vec<u8>,

    // Размер уже обрезанного кадра.
    width: u32,
    height: u32,

    // Флаг: появился новый кадр.
    new_frame: bool,

    // Координаты зоны захвата внутри окна игры.
    // Эти координаты приходят из Python как left_offset/top_offset/width/height.
    crop_x: u32,
    crop_y: u32,
    crop_w: u32,
    crop_h: u32,
}

struct CaptureHandler {
    state: Arc<Mutex<CaptureState>>,
}

impl GraphicsCaptureApiHandler for CaptureHandler {
    type Flags = Arc<Mutex<CaptureState>>;
    type Error = Box<dyn std::error::Error + Send + Sync>;

    fn new(ctx: Context<Self::Flags>) -> Result<Self, Self::Error> {
        Ok(Self {
            state: ctx.flags.clone(),
        })
    }

    fn on_frame_arrived(
        &mut self,
        frame: &mut Frame,
        _capture_control: InternalCaptureControl,
    ) -> Result<(), Self::Error> {
        // Получаем буфер кадра от Windows Graphics Capture.
        let mut buffer = frame.buffer()?;

        // Размер видимой области кадра.
        // Обычно совпадает с frame.width()/frame.height(), но логичнее брать
        // значения у самого буфера, потому что именно его мы читаем.
        let frame_width = buffer.width();
        let frame_height = buffer.height();

        // Критически важный параметр: реальная длина одной строки в байтах.
        // Она может быть больше, чем frame_width * 4, из-за выравнивания.
        let row_pitch = buffer.row_pitch();

        // Сырой буфер может содержать padding в конце строк.
        let raw_buffer = buffer.as_raw_buffer();

        if let Ok(mut state) = self.state.lock() {
            let cx = state.crop_x;
            let cy = state.crop_y;
            let cw = state.crop_w;
            let ch = state.crop_h;

            // Валидация границ кропа.
            // Если рамка ушла за пределы окна игры или игра свернута,
            // не возвращаем битый кадр в Python.
            if cw == 0 || ch == 0 || cx >= frame_width || cy >= frame_height {
                state.new_frame = false;
                return Ok(());
            }

            let safe_cw = cw.min(frame_width.saturating_sub(cx));
            let safe_ch = ch.min(frame_height.saturating_sub(cy));

            if safe_cw == 0 || safe_ch == 0 {
                state.new_frame = false;
                return Ok(());
            }

            // Выделяем память под плотный RGBA-буфер без padding.
            // Именно такой формат ожидает Python/PIL:
            // Image.frombytes("RGBA", (width, height), raw_bytes)
            let mut cropped_data = Vec::with_capacity((safe_cw * safe_ch * 4) as usize);

            // Построчно копируем только нужную зону.
            // Главное отличие от старого варианта: используем row_pitch.
            for y in cy..(cy + safe_ch) {
                let start_idx = (y as usize * row_pitch as usize) + (cx as usize * 4);
                let end_idx = start_idx + (safe_cw as usize * 4);

                if end_idx <= raw_buffer.len() {
                    cropped_data.extend_from_slice(&raw_buffer[start_idx..end_idx]);
                } else {
                    // Если Windows неожиданно отдала неполный буфер,
                    // лучше пропустить кадр, чем отправить мусор в OCR.
                    state.new_frame = false;
                    return Ok(());
                }
            }

            state.data = cropped_data;
            state.width = safe_cw;
            state.height = safe_ch;
            state.new_frame = true;
        }

        Ok(())
    }

    fn on_closed(&mut self) -> Result<(), Self::Error> {
        Ok(())
    }
}

#[pyclass]
struct RustCaptureEngine {
    state: Arc<Mutex<CaptureState>>,
    capture_control: Option<
        CaptureControl<CaptureHandler, Box<dyn std::error::Error + Send + Sync>>,
    >,
}

impl RustCaptureEngine {
    fn stop_capture(&mut self) -> Result<(), String> {
        let Some(capture_control) = self.capture_control.take() else {
            return Ok(());
        };

        capture_control.stop().map_err(|error| error.to_string())
    }
}

impl Drop for RustCaptureEngine {
    fn drop(&mut self) {
        // Drop is only a safety net. Production calls stop() explicitly.
        let _ = self.stop_capture();
    }
}

#[pymethods]
impl RustCaptureEngine {
    #[new]
    fn new(hwnd_address: usize, crop_x: u32, crop_y: u32, crop_w: u32, crop_h: u32) -> PyResult<Self> {
        let state = Arc::new(Mutex::new(CaptureState {
            data: Vec::new(),
            width: 0,
            height: 0,
            new_frame: false,
            crop_x,
            crop_y,
            crop_w,
            crop_h,
        }));

        let window = Window::from_raw_hwnd(hwnd_address as *mut std::ffi::c_void);
        let settings = Settings::new(
            window,
            CursorCaptureSettings::WithoutCursor,
            DrawBorderSettings::WithoutBorder,
            SecondaryWindowSettings::Default,
            MinimumUpdateIntervalSettings::Default,
            DirtyRegionSettings::Default,
            ColorFormat::Rgba8,
            state.clone(),
        );
        let capture_control = CaptureHandler::start_free_threaded(settings)
            .map_err(|error| pyo3::exceptions::PyRuntimeError::new_err(error.to_string()))?;

        Ok(RustCaptureEngine {
            state,
            capture_control: Some(capture_control),
        })
    }

    fn stop(&mut self) -> PyResult<()> {
        self.stop_capture()
            .map_err(pyo3::exceptions::PyRuntimeError::new_err)
    }

    fn get_next_frame(&mut self, py: Python) -> PyResult<PyObject> {
        if let Ok(mut state) = self.state.lock() {
            if state.new_frame && !state.data.is_empty() {
                state.new_frame = false;

                let py_bytes = pyo3::types::PyBytes::new(py, &state.data);
                let tuple = (py_bytes, state.width, state.height);

                return Ok(tuple.into_pyobject(py)?.into_any().unbind());
            }
        }

        Ok(py.None())
    }
}

#[pymodule]
fn game_capture_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RustCaptureEngine>()?;
    Ok(())
}
