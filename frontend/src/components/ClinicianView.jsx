import React, { useState, useEffect } from 'react';

const API_BASE_URL = import.meta.env.VITE_BACKEND_URL || '';

export default function ClinicianView({ clinicianId = "1" }) {
  const [schedule, setSchedule] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function loadClinicianSchedule() {
      try {
        setLoading(true);
        const res = await fetch(`${API_BASE_URL}/api/clinicians/${clinicianId}/schedule`);
        if (!res.ok) throw new Error('Could not load daily clinician schedule.');
        const data = await res.json();
        setSchedule(data);
      } catch (err) {
        console.error(err);
      } finally {
        setLoading(false);
      }
    }

    loadClinicianSchedule();
  }, [clinicianId]);

  return (
    <div className="p-6 max-w-5xl mx-auto space-y-6">
      <div className="border-b border-gray-200 pb-4">
        <h1 className="text-2xl font-bold text-gray-900">Clinician Schedule & Agenda</h1>
        <p className="text-sm text-gray-500">Daily appointment roster and patient check-in queue.</p>
      </div>

      {loading ? (
        <div className="text-center py-10 text-gray-500">Loading daily agenda...</div>
      ) : schedule.length === 0 ? (
        <div className="bg-gray-50 p-6 rounded-lg text-center text-gray-500 border border-gray-200">
          No appointments scheduled for today.
        </div>
      ) : (
        <div className="grid gap-4">
          {schedule.map((item) => (
            <div key={item.id} className="flex flex-col sm:flex-row justify-between items-start sm:items-center p-4 bg-white border border-gray-200 rounded-lg shadow-sm">
              <div>
                <div className="text-xs font-semibold text-blue-600 uppercase tracking-wide">
                  {new Date(item.appointment_time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                </div>
                <div className="text-lg font-semibold text-gray-900">{item.patient_name}</div>
                <div className="text-sm text-gray-500">Reason: {item.reason || 'Standard Outpatient Consultation'}</div>
              </div>
              <div className="mt-3 sm:mt-0">
                <span className={`px-3 py-1 rounded-full text-xs font-medium ${
                  item.status === 'confirmed' ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-700'
                }`}>
                  {item.status}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
